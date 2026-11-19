# The sources each package needs (inside a flake only tracked files exist), so unrelated edits (docs, Docker files, the MLX server)
# do not trigger a rebuild.
{ lib }:
{
  web = lib.fileset.toSource {
    root = ../web;
    fileset = ../web;
  };
  server = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.fileFilter (file: file.hasExt "py") ../.;
  };
}
