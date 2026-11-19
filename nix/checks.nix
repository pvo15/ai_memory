# `nix flake check`: both packages build (the dashboard build includes the TypeScript type
# check) and every server module imports. The repo has no test suite.
{ self, pkgs }:
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
}
