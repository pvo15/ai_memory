"""Tiny embedding server on Apple Silicon (MLX, runs on the GPU).

Speaks the same API as Ollama's embed endpoint, so mini-codesearch doesn't care
which one it talks to:

    POST /api/embed   {"input": ["text", ...]}  ->  {"embeddings": [[...], ...]}
    POST /api/generate {"prompt": "...", "system": "...", "max_tokens": 700}
                      -> plain text, STREAMED piece by piece (local chat model)
    GET  /health      -> {"ok": true, "model": "...", "llm_loaded": bool}

The chat model (default Qwen2.5-Coder-7B-Instruct, 4-bit, ~4 GB RAM) is loaded only on
the first /api/generate call and freed again after LLM_IDLE_SECONDS without use, so it
costs nothing while unused. One GPU job runs at a time (embedding waits during generation).

How Qwen3-Embedding works (it's a Qwen3 LLM trained for embeddings):
  1. tokenize the text and append the end-of-text token
  2. run the transformer, but skip the final "predict next word" layer
  3. the hidden state of that LAST token summarises the whole text -> 1024 numbers
  4. L2-normalise it, so cosine similarity == dot product

Run:  ./mlx-server/run.sh     (needs a Python with mlx-lm installed)
"""

import gc
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

import mlx.core as mx
from mlx_lm import load, stream_generate
from mlx_lm.sample_utils import make_sampler

MODEL_ID = os.environ.get("MCS_MLX_MODEL", "mlx-community/Qwen3-Embedding-0.6B-4bit-DWQ")
HOST = os.environ.get("MCS_MLX_HOST", "127.0.0.1")  # localhost only
PORT = int(os.environ.get("MCS_MLX_PORT", "18765"))
MAX_TOKENS = 2048

print(f"Loading {MODEL_ID} (downloads to ~/.cache/huggingface on first run)…", flush=True)
model, tokenizer = load(MODEL_ID)
EOS_ID = tokenizer.convert_tokens_to_ids("<|endoftext|>")
PAD_ID = EOS_ID
gpu_lock = Lock()  # one GPU job at a time

# ---------------------------------------------------------------- local chat model (lazy)
LLM_ID = os.environ.get("MCS_LLM_MODEL", "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit")
LLM_IDLE_SECONDS = int(os.environ.get("LLM_IDLE_SECONDS", "600"))
_llm = {"model": None, "tok": None, "last": 0.0}
_llm_load_lock = Lock()
_gen = {"current": 0}  # id of the newest generation request
_gen_lock = Lock()


def get_llm():
    with _llm_load_lock:
        if _llm["model"] is None:
            t0 = time.time()
            print(f"loading chat model {LLM_ID}…", flush=True)
            _llm["model"], _llm["tok"] = load(LLM_ID)
            print(f"chat model ready in {time.time() - t0:.1f}s", flush=True)
        _llm["last"] = time.time()
        return _llm["model"], _llm["tok"]


def _unload_when_idle():
    while True:
        time.sleep(60)
        with _llm_load_lock:
            if _llm["model"] is not None and time.time() - _llm["last"] > LLM_IDLE_SECONDS:
                with gpu_lock:
                    _llm["model"] = _llm["tok"] = None
                    gc.collect()
                    mx.clear_cache()
                print("chat model unloaded (idle) - memory freed", flush=True)


threading.Thread(target=_unload_when_idle, daemon=True).start()


def embed(texts: list[str]) -> list[list[float]]:
    # 1. tokenize + append end-of-text token
    seqs = [tokenizer.encode(t)[: MAX_TOKENS - 1] + [EOS_ID] for t in texts]
    lengths = [len(s) for s in seqs]

    # Batch with RIGHT padding. The model uses causal attention (each token only
    # sees tokens before it), so padding added AFTER a sequence can't change
    # its last real token — no attention mask needed.
    width = max(lengths)
    batch = mx.array([s + [PAD_ID] * (width - len(s)) for s in seqs])

    # 2. model.model = transformer without the next-word head -> hidden states
    with gpu_lock:
        hidden = model.model(batch)  # (batch, tokens, 1024)
        # 3. pick the hidden state of each sequence's last real token
        last = hidden[mx.arange(len(seqs)), mx.array(lengths) - 1].astype(mx.float32)
        # 4. L2-normalise (in float32 — the model computes in 16-bit, which is
        #    too imprecise for the norm)
        last = last / mx.linalg.norm(last, axis=-1, keepdims=True)
        mx.eval(last)
    return last.tolist()


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"ok": True, "model": MODEL_ID, "llm": LLM_ID, "llm_loaded": _llm["model"] is not None})
        else:
            self._send(404, {"error": "not found"})

    def generate(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        model, tok = get_llm()
        messages = ([{"role": "system", "content": body["system"]}] if body.get("system") else []) + \
                   [{"role": "user", "content": body["prompt"]}]
        prompt = tok.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        # Newest request wins: a new explanation makes any older one still running stop,
        # so abandoned requests never queue up on the GPU.
        with _gen_lock:
            _gen["current"] += 1
            my_turn = _gen["current"]
        t0, n, why = time.time(), 0, "done"
        tokens = stream_generate(model, tok, prompt, max_tokens=int(body.get("max_tokens", 700)),
                                 sampler=make_sampler(temp=float(body.get("temperature", 0.2))))
        while True:
            if _gen["current"] != my_turn:
                why = "replaced by a newer request"
                break
            # Hold the GPU for ONE token at a time, so embeddings (code search, indexing)
            # get their turn in between instead of waiting for the whole answer.
            with gpu_lock:
                try:
                    r = next(tokens)
                except StopIteration:
                    break
            try:
                self.wfile.write(r.text.encode())
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                why = "client closed"  # page closed / Stop pressed: stop generating
                break
            n += 1
        _llm["last"] = time.time()
        print(f"generated {n} tokens in {time.time() - t0:.1f}s ({why})", flush=True)

    def do_POST(self) -> None:
        if self.path == "/api/generate":
            try:
                self.generate()
            except Exception as err:
                try:
                    self._send(500, {"error": str(err)})
                except Exception:
                    pass
            return
        if self.path != "/api/embed":
            self._send(404, {"error": "not found"})
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            texts = body["input"] if isinstance(body["input"], list) else [body["input"]]
            t0 = time.time()
            vectors = embed(texts)
            print(f"embedded {len(texts)} texts in {time.time() - t0:.2f}s", flush=True)
            self._send(200, {"model": MODEL_ID, "embeddings": vectors})
        except Exception as err:  # report the error to the client instead of crashing
            self._send(500, {"error": str(err)})

    def log_message(self, *_args) -> None:  # silence default per-request logging
        pass


if __name__ == "__main__":
    print(f"MLX embedding server on http://{HOST}:{PORT}  (device: {mx.default_device()})", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
