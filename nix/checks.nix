# `nix flake check`: both packages build (the dashboard build includes the TypeScript type
# check) and every server module imports. The repo has no test suite.
{ pkgs }:
{
  inherit (pkgs) ai-memory ai-memory-web;
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
