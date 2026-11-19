# `nix run .` starts the server (set MEMORY_TOKEN first).
{ pkgs }:
rec {
  default = ai-memory;
  ai-memory = {
    type = "app";
    program = "${pkgs.ai-memory}/bin/ai-memory";
    meta.description = "Start the ai-memory server";
  };
}
