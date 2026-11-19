# The server: the repo's Python files, a Python environment with its dependencies and a
# wrapper with defaults for the paths that the Docker image hard-codes (/data, /backups, /app/web).
{
  lib,
  stdenvNoCC,
  python3,
  git,
  makeWrapper,
  ai-memory-web,
}:
let
  # The repo pins mcp==1.30.0 and uses the v1 `mcp.server.fastmcp` API; mcp 2.x removed it,
  # so the v1 line from nixpkgs (mcp_1) is used.
  python = python3.withPackages (ps: [
    ps.mcp_1
    ps.uvicorn
    ps.numpy
    ps.tzdata
  ]);
in
stdenvNoCC.mkDerivation {
  pname = "ai-memory";
  version = "0-unstable";
  src = (import ./source.nix { inherit lib; }).server;

  nativeBuildInputs = [ makeWrapper ];

  dontBuild = true;

  installPhase = ''
    runHook preInstall
    mkdir -p $out/share/ai-memory
    cp *.py $out/share/ai-memory/
    # Data lives under $XDG_DATA_HOME/ai-memory unless MEMORY_DB / BACKUP_DIR are set.
    # The HTTP server refuses to start without MEMORY_TOKEN. MLX is Apple-only: off by default.
    makeWrapper ${python}/bin/python $out/bin/ai-memory \
      --add-flags $out/share/ai-memory/memory_server.py \
      --prefix PATH : ${lib.makeBinPath [ git ]} \
      --set-default WEB_DIR ${ai-memory-web} \
      --set-default MEMORY_HOST 127.0.0.1 \
      --set-default MLX off \
      --run 'data="''${XDG_DATA_HOME:-$HOME/.local/share}/ai-memory"' \
      --run 'export MEMORY_DB="''${MEMORY_DB:-$data/memory.db}"' \
      --run 'export BACKUP_DIR="''${BACKUP_DIR:-$data/backups}"' \
      --run 'export MLX_LOG="''${MLX_LOG:-$data/mlx-embed.log}"'
    runHook postInstall
  '';

  meta = {
    description = "Self-hosted shared memory server for AI tools (MCP)";
    mainProgram = "ai-memory";
    platforms = lib.platforms.unix;
  };
}
