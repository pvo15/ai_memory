"""
Running-job registry, so any long job can be watched and STOPPED from the dashboard.

Every long task (code index, Jira sync, backup, AI explanation) registers here with a
stop flag. The task checks the flag between steps (between embedding batches, Jira
pages, generated text pieces) and ends cleanly: work already saved stays saved, and the
next run continues from there (indexing is resumable, Jira sync is idempotent).
"""
import itertools
import threading
import time
from collections import deque
from contextlib import contextmanager

_ids = itertools.count(1)
_lock = threading.Lock()
RUNNING: dict[int, dict] = {}
HISTORY: deque = deque(maxlen=30)


class Stopped(Exception):
    """Raised inside a job when the user pressed Stop."""


@contextmanager
def job(kind: str, label: str, source: str = "auto"):
    """with jobs.job("code_index", "Index my-app") as j: ... j.check(); j.progress = "..." """
    j = _Job(next(_ids), kind, label, source)
    with _lock:
        RUNNING[j.id] = j.info
    try:
        yield j
        j.finish("stopped" if j.stopped else "done")
    except Stopped:
        j.finish("stopped")
        raise
    except Exception as e:
        j.finish("error", str(e))
        raise


class _Job:
    def __init__(self, id_: int, kind: str, label: str, source: str):
        self.id = id_
        self.stop_event = threading.Event()
        self.info = {"id": id_, "kind": kind, "label": label, "source": source, "state": "running",
                     "started": time.time(), "progress": "", "_job": self}

    @property
    def progress(self) -> str:
        return self.info["progress"]

    @progress.setter
    def progress(self, text: str) -> None:
        self.info["progress"] = text

    def check(self) -> None:
        if self.stop_event.is_set():
            raise Stopped()

    @property
    def stopped(self) -> bool:
        return self.stop_event.is_set()

    def finish(self, state: str, error: str = "") -> None:
        with _lock:
            RUNNING.pop(self.id, None)
            self.info.update(state=state, finished=time.time(), error=error)
            HISTORY.appendleft(self.info)


def stop(job_id: int) -> bool:
    with _lock:
        info = RUNNING.get(job_id)
    if not info:
        return False
    info["_job"].stop_event.set()
    info["state"] = "stopping"
    return True


def stop_all(kind: str = "") -> int:
    with _lock:
        ids = [i for i, info in RUNNING.items() if not kind or info["kind"] == kind]
    return sum(stop(i) for i in ids)


def snapshot() -> dict:
    clean = lambda d: {k: v for k, v in d.items() if not k.startswith("_")}  # noqa: E731
    with _lock:
        return {"running": [clean(i) for i in RUNNING.values()], "recent": [clean(i) for i in list(HISTORY)[:15]]}
