# Assembles the flake outputs from the files in this directory.
{ self, nixpkgs }:
let
  # The server and the dashboard are platform-neutral; only the optional MLX server is
  # Apple-only and it is not packaged here.
  systems = [
    "x86_64-linux"
    "aarch64-linux"
    "aarch64-darwin"
    "x86_64-darwin"
  ];
  forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f system);
  pkgsFor =
    system:
    import nixpkgs {
      inherit system;
      overlays = [ self.overlays.default ];
    };
in
{
  overlays.default = import ./overlay.nix;

  packages = forAllSystems (
    system:
    let
      pkgs = pkgsFor system;
    in
    {
      default = pkgs.ai-memory;
      ai-memory = pkgs.ai-memory;
      ai-memory-web = pkgs.ai-memory-web;
    }
  );

  apps = forAllSystems (system: import ./apps.nix { pkgs = pkgsFor system; });

  devShells = forAllSystems (system: {
    default = import ./devshell.nix { pkgs = pkgsFor system; };
  });

  checks = forAllSystems (system: import ./checks.nix { pkgs = pkgsFor system; });

  formatter = forAllSystems (system: (pkgsFor system).nixfmt-tree);
}
