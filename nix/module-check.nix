# A flake check: evaluates the Home Manager module against a minimal stub of the options it
# uses (Home Manager itself is not an input of this flake), then inspects the generated
# launcher, service and hook.
{ self, pkgs }:
let
  inherit (pkgs) lib;

  # Only what the module touches in Home Manager.
  stub =
    { lib, ... }:
    {
      options = {
        assertions = lib.mkOption {
          type = lib.types.listOf lib.types.unspecified;
          default = [ ];
        };
        home.packages = lib.mkOption {
          type = lib.types.listOf lib.types.package;
          default = [ ];
        };
        xdg.dataHome = lib.mkOption {
          type = lib.types.str;
          default = "/home/user/.local/share";
        };
        systemd.user.tmpfiles.rules = lib.mkOption {
          type = lib.types.listOf lib.types.str;
          default = [ ];
        };
        systemd.user.services = lib.mkOption {
          type = lib.types.attrsOf lib.types.attrs;
          default = { };
        };
        launchd.agents = lib.mkOption {
          type = lib.types.attrsOf lib.types.attrs;
          default = { };
        };
        # a sops-nix secret, as the module sees it
        sops.secrets = lib.mkOption {
          type = lib.types.attrsOf (
            lib.types.submodule {
              options.path = lib.mkOption { type = lib.types.str; };
            }
          );
          default = { };
        };
      };
    };

  evaluate =
    extra:
    lib.evalModules {
      modules = [
        stub
        self.homeManagerModules.default
        (
          { config, ... }:
          {
            _module.args.pkgs = pkgs;
            sops.secrets.token.path = "/run/secrets/ai-memory-token";
            services.ai-memory = {
              enable = true;
              tokenFile = config.sops.secrets.token.path;
              environment = {
                PROJECTS_DIR = "/home/user/src";
                CODE_PROJECTS = "app,api";
              };
              environmentFile = "/run/secrets/ai-memory-env";
            };
          }
        )
        extra
      ];
    };

  ok = evaluate { };
  cfg = ok.config.services.ai-memory;
  unit = ok.config.systemd.user.services.ai-memory;
  failed = lib.filter (a: !a.assertion) ok.config.assertions;
  inStore = evaluate { services.ai-memory.tokenFile = lib.mkForce "${pkgs.hello}/token"; };
  storeFailed = lib.filter (a: !a.assertion) inStore.config.assertions;
in
assert failed == [ ];
assert lib.length storeFailed == 1;
assert cfg.mcpUrl == "http://127.0.0.1:8765/mcp";
assert
  ok.config.systemd.user.tmpfiles.rules == [ "d /home/user/.local/share/ai-memory 0700 - - -" ];
assert unit.Service.Restart == "on-failure";
pkgs.runCommand "ai-memory-module-check" { } ''
  launcher=${lib.escapeShellArg (lib.removePrefix "ExecStart=" unit.Service.ExecStart)}
  hook=${lib.escapeShellArg cfg.hookCommand}

  # the launcher reads the token at run time and holds no secret
  grep -q 'run/secrets/ai-memory-token' "$launcher"
  grep -q 'export PROJECTS_DIR=/home/user/src' "$launcher"
  grep -q 'run/secrets/ai-memory-env' "$launcher"
  grep -q 'MEMORY_PORT=8765' "$launcher"
  ${pkgs.bash}/bin/bash -n "$launcher"

  # the hook finds its token through a symlink to the file the launcher writes
  test -x "$hook"
  test "$(readlink "$(dirname "$hook")/../.env")" = /home/user/.local/share/ai-memory/hook.env
  test -x "$(dirname "$hook")/../hooks/session-start.sh"
  grep -q 'AI_MEMORY_URL' "$hook"

  # hook with no token file: prints nothing, exits 0
  briefing=$("$hook" < /dev/null)
  test -z "$briefing"
  touch $out
''
