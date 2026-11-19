{
  description = "ai-memory: a self-hosted shared memory server for AI tools (MCP)";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }: import ./nix { inherit self nixpkgs; };
}
