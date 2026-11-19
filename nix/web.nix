# The React dashboard, built with Vite. Served by the server under /ui.
{
  lib,
  stdenvNoCC,
  nodejs,
  pnpm_11,
  fetchPnpmDeps,
  pnpmConfigHook,
}:
stdenvNoCC.mkDerivation (finalAttrs: {
  pname = "ai-memory-web";
  version = "1.0.0";
  src = (import ./source.nix { inherit lib; }).web;

  pnpmDeps = fetchPnpmDeps {
    inherit (finalAttrs) pname version src;
    pnpm = pnpm_11;
    fetcherVersion = 4;
    # Update after changing web/pnpm-lock.yaml: set to lib.fakeHash and copy the hash from the error.
    hash = "sha256-0osh64S2TWAblc8hU2k14ra91q7eFfZ5V0M1eZLuAKo=";
  };

  nativeBuildInputs = [
    nodejs
    pnpm_11
    pnpmConfigHook
  ];

  buildPhase = ''
    runHook preBuild
    pnpm build
    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall
    cp -r dist $out
    runHook postInstall
  '';

  meta.description = "ai-memory dashboard (static bundle)";
})
