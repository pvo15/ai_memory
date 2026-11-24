# `nix flake check`: both packages build (the dashboard build includes the TypeScript type
# check) and every server module imports, and the unit tests in tests/ pass.
{ self, pkgs }:
let
  python = pkgs.python3.withPackages (ps: [
    ps.mcp_1
    ps.uvicorn
    ps.numpy
    ps.tzdata
  ]);
in
{
  inherit (pkgs) ai-memory ai-memory-web;
  home-manager-module = import ./module-check.nix { inherit self pkgs; };
  imports =
    pkgs.runCommand "ai-memory-imports"
      {
        nativeBuildInputs = [ pkgs.ai-memory ];
      }
      ''
        export HOME=$TMPDIR MEMORY_DB=$TMPDIR/memory.db
        ai-memory --help > /dev/null
        touch $out
      '';
  unit-tests =
    pkgs.runCommand "ai-memory-unit-tests"
      {
        nativeBuildInputs = [ python ];
        src = (import ./source.nix { inherit (pkgs) lib; }).server;
      }
      ''
        export HOME=$TMPDIR MEMORY_DB=$TMPDIR/memory.db
        cp -r $src/. src && chmod -R u+w src && cd src
        python -m unittest discover -s tests -v
        touch $out
      '';
}
