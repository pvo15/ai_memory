# The development shell: the server's Python environment plus the dashboard toolchain.
{ pkgs }:
pkgs.mkShell {
  packages = with pkgs; [
    (python3.withPackages (ps: [
      ps.mcp_1
      ps.uvicorn
      ps.numpy
      ps.tzdata
    ]))
    nodejs
    pnpm_11
    git
  ];
}
