# Adds the server and the dashboard bundle to a package set.
final: _prev: {
  ai-memory-web = final.callPackage ./web.nix { };
  ai-memory = final.callPackage ./package.nix { };
}
