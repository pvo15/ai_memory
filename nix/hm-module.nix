# Home Manager module: runs the server as a user service (systemd on Linux, launchd on
# macOS) and provides the session hook and the MCP URL for wiring up agents. Used as
#   imports = [ inputs.ai-memory.homeManagerModules.default ];
#   services.ai-memory = { enable = true; tokenFile = config.sops.secrets.ai-memory-token.path; };
# The token is a file path only and never enters the Nix store. The module changes no agent
# configuration: a consumer points the agent at `mcpUrl` and `hookCommand`.
self:
{
  config,
  lib,
  pkgs,
  ...
}:
let
  inherit (lib)
    mkEnableOption
    mkIf
    mkMerge
    mkOption
    types
    ;
  cfg = config.services.ai-memory;
  inStore = path: lib.hasPrefix builtins.storeDir (toString path);

  # `0.0.0.0` listens everywhere but is not an address a client can use.
  clientHost = if cfg.host == "0.0.0.0" then "127.0.0.1" else cfg.host;
  baseUrl = "http://${clientHost}:${toString cfg.port}";

  # The hook reads its token from `.env` next to the script's parent directory. The store is
  # read-only and holds no secrets, so the hook is copied into a folder whose `.env` is a
  # symlink to the file the launcher writes under dataDir at every start.
  hookFile = "${cfg.dataDir}/hook.env";
  hookDir = pkgs.runCommandLocal "ai-memory-hook" { nativeBuildInputs = [ pkgs.makeWrapper ]; } ''
    mkdir -p $out/hooks $out/bin
    install -m755 ${../hooks/session-start.sh} $out/hooks/session-start.sh
    patchShebangs $out/hooks/session-start.sh
    ln -s ${lib.escapeShellArg hookFile} $out/.env
    makeWrapper $out/hooks/session-start.sh $out/bin/ai-memory-session-start \
      --prefix PATH : ${
        lib.makeBinPath [
          pkgs.python3
          pkgs.curl
          pkgs.git
          pkgs.coreutils
          pkgs.gnugrep
        ]
      } \
      --set-default AI_MEMORY_URL ${lib.escapeShellArg baseUrl}
  '';

  # Prints nothing of the secrets. Order: plain `environment`, then `environmentFile`
  # (which may override it), then the token and the paths this module owns.
  launcher = pkgs.writeShellScript "ai-memory-launcher" ''
    set -eu
    ${lib.concatStringsSep "\n" (
      lib.mapAttrsToList (n: v: "export ${n}=${lib.escapeShellArg (toString v)}") cfg.environment
    )}
    ${lib.optionalString (cfg.environmentFile != null) ''
      if [ ! -r ${lib.escapeShellArg cfg.environmentFile} ]; then
        echo "ai-memory: cannot read environmentFile" >&2
        exit 1
      fi
      set -a
      . ${lib.escapeShellArg cfg.environmentFile}
      set +a
    ''}
    export MEMORY_HOST=${lib.escapeShellArg cfg.host}
    export MEMORY_PORT=${toString cfg.port}
    export MEMORY_DB=${lib.escapeShellArg "${cfg.dataDir}/memory.db"}
    export BACKUP_DIR="''${BACKUP_DIR:-${lib.escapeShellArg cfg.dataDir}/backups}"
    export MLX_LOG="''${MLX_LOG:-${lib.escapeShellArg cfg.dataDir}/mlx-embed.log}"
    umask 077
    mkdir -p ${lib.escapeShellArg cfg.dataDir}
    if [ ! -r ${lib.escapeShellArg cfg.tokenFile} ]; then
      echo "ai-memory: cannot read tokenFile" >&2
      exit 1
    fi
    token=$(${pkgs.coreutils}/bin/tr -d '\r\n' < ${lib.escapeShellArg cfg.tokenFile})
    if [ -z "$token" ]; then
      echo "ai-memory: tokenFile is empty" >&2
      exit 1
    fi
    export MEMORY_TOKEN="$token"
    # For the session hook (see hookCommand); mode 600 through the umask above.
    printf 'MEMORY_TOKEN=%s\n' "$token" > ${lib.escapeShellArg hookFile}.tmp
    mv ${lib.escapeShellArg hookFile}.tmp ${lib.escapeShellArg hookFile}
    unset token
    exec ${lib.getExe cfg.package}
  '';
in
{
  options.services.ai-memory = {
    enable = mkEnableOption "the ai-memory server, a shared memory for AI tools (MCP)";

    package = mkOption {
      type = types.package;
      default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
      defaultText = lib.literalExpression "ai-memory from the flake that provides this module";
      description = "The ai-memory package.";
    };

    host = mkOption {
      type = types.str;
      default = "127.0.0.1";
      description = ''
        Address the server listens on. The server only accepts the host names `localhost` and
        `127.0.0.1` in requests (DNS-rebinding protection), so other values mostly make sense
        behind a proxy that rewrites the Host header.
      '';
    };

    port = mkOption {
      type = types.port;
      default = 8765;
      description = "TCP port of the MCP endpoint (`/mcp`), the dashboard (`/ui`) and `/health`.";
    };

    dataDir = mkOption {
      type = types.str;
      default = "${config.xdg.dataHome}/ai-memory";
      defaultText = lib.literalExpression ''"''${config.xdg.dataHome}/ai-memory"'';
      description = ''
        Where the SQLite database (`memory.db`), the backups, the team-sync clone and the
        generated hook token file (`hook.env`) live. Created with mode 700.
      '';
    };

    tokenFile = mkOption {
      type = types.str;
      description = ''
        A file holding only the bearer token that protects the server (the HTTP server refuses
        to start without one). Read at every start; a trailing newline is ignored. Give the
        path, for example a sops-nix or agenix secret, never the token itself and never a path
        in the Nix store. Create one with `openssl rand -hex 24`.
      '';
      example = lib.literalExpression "config.sops.secrets.ai-memory-token.path";
    };

    environment = mkOption {
      type = types.attrsOf (
        types.oneOf [
          types.str
          types.int
          types.bool
        ]
      );
      default = { };
      description = ''
        Other environment variables of the server. The values end up in the Nix store: put
        secrets in `environmentFile` instead. The ones you will most likely want:

        - `PROJECTS_DIR`: the folder with your repositories (read-only for the server).
        - `CODE_PROJECTS`: comma-separated repository folder names inside `PROJECTS_DIR`
          that are indexed for code search and explanation. More can be connected at run
          time on the dashboard or with the `connect_project` tool (stored in the database).
        - `PROJECT_GROUPS`: repositories that are one product, with `+` between them.
        - `CONNECT_HINT_SKIP`: repository folder names whose sessions skip the "not connected
          to ai-memory" note (`*` = never show it).
        - `SESSION_MEMORIES`: how many of the project's newest memories the session hook shows
          at session start, one line each (default 15, `0` = off).
        - `STALE_TASK_DAYS`, `NEVER_RECALLED_DAYS`: thresholds of the dashboard's Clean-up tab
          (defaults 7 and 14 days).
        - `JIRA_URL`, `JIRA_EMAIL`, `JIRA_SYNC_PROJECT` (and `JIRA_API_TOKEN`, a secret):
          Jira story sync. Tuning: `JIRA_SYNC_DAYS`, `JIRA_SYNC_INTERVAL_HOURS`,
          `JIRA_SYNC_TYPES`, `AUTO_TASK_PREFIXES`.
        - `SYNC_REPO_URL`, `SYNC_AUTHOR`, `SYNC_BRANCH`, `SYNC_INTERVAL_MINUTES`,
          `SYNC_GRACE_MINUTES` (and `SYNC_GIT_TOKEN`, a secret): team memory sync.
        - `BACKUP_HOUR`, `BACKUP_WEEKDAYS`, `BACKUP_KEEP`, `BACKUP_TZ`, `TZ`: backups and
          log time zone.
        - `CODE_REINDEX_SECONDS`: how often the code index refreshes.
        - `MLX`: `off` by default (the MLX GPU server is Apple Silicon only).

        `MEMORY_HOST`, `MEMORY_PORT`, `MEMORY_DB` and `MEMORY_TOKEN` are set from the options
        of this module and cannot be overridden here.
      '';
      example = {
        PROJECTS_DIR = "/home/user/src";
        CODE_PROJECTS = "app,api";
      };
    };

    environmentFile = mkOption {
      type = types.nullOr types.str;
      default = null;
      description = ''
        An optional file with more variables, for secrets such as `JIRA_API_TOKEN` and
        `SYNC_GIT_TOKEN`: lines of `NAME=value`, read by the launcher as shell syntax (quote
        values with spaces or special characters). Applied after `environment`, so it wins.
        Never a path in the Nix store.
      '';
      example = lib.literalExpression "config.sops.secrets.ai-memory-env.path";
    };

    mcpUrl = mkOption {
      type = types.str;
      readOnly = true;
      default = "${baseUrl}/mcp";
      description = "URL of the MCP endpoint (streamable HTTP, `Authorization: Bearer <token>`).";
    };

    hookCommand = mkOption {
      type = types.str;
      readOnly = true;
      default = "${hookDir}/bin/ai-memory-session-start";
      description = ''
        The SessionStart hook for Claude Code: it prints the current task's briefing. It reads
        the token from the file the service writes at start, so it prints nothing while the
        service has not run yet. Append `--json` for clients that expect JSON output. Use it
        with `--end` as the SessionEnd hook too: it adds a short "where the session stopped"
        note to the task journal. Both copy Claude Code's own memory files into ai-memory.
      '';
    };
  };

  config = mkIf cfg.enable (mkMerge [
    {
      assertions = [
        {
          assertion = lib.hasPrefix "/" cfg.tokenFile && !inStore cfg.tokenFile;
          message = "services.ai-memory.tokenFile must be an absolute path outside the Nix store (for example a sops-nix secret path); the store is world-readable.";
        }
        {
          assertion =
            cfg.environmentFile == null
            || (lib.hasPrefix "/" cfg.environmentFile && !inStore cfg.environmentFile);
          message = "services.ai-memory.environmentFile must be an absolute path outside the Nix store.";
        }
        {
          assertion = lib.hasPrefix "/" cfg.dataDir;
          message = "services.ai-memory.dataDir must be an absolute path.";
        }
        {
          assertion = lib.all (
            n:
            !lib.elem n [
              "MEMORY_HOST"
              "MEMORY_PORT"
              "MEMORY_DB"
              "MEMORY_TOKEN"
            ]
          ) (lib.attrNames cfg.environment);
          message = "services.ai-memory.environment: MEMORY_HOST, MEMORY_PORT, MEMORY_DB and MEMORY_TOKEN come from the module options.";
        }
      ];

      home.packages = [ cfg.package ];
    }

    (mkIf pkgs.stdenv.hostPlatform.isLinux {
      # The service's file-system sandbox needs the directory to exist before it starts.
      systemd.user.tmpfiles.rules = [ "d ${cfg.dataDir} 0700 - - -" ];

      systemd.user.services.ai-memory = {
        Unit = {
          Description = "ai-memory server";
          After = [ "network.target" ];
        };
        Service = {
          ExecStart = toString launcher;
          Restart = "on-failure";
          RestartSec = 5;
          # Light hardening. The repositories in CODE_PROJECTS stay readable for git
          # (ProtectHome is read-only, not hidden); only dataDir is writable.
          NoNewPrivileges = true;
          PrivateTmp = true;
          ProtectSystem = "strict";
          ProtectHome = "read-only";
          ReadWritePaths = [ "-${cfg.dataDir}" ];
          RestrictSUIDSGID = true;
          LockPersonality = true;
        };
        Install.WantedBy = [ "default.target" ];
      };
    })

    (mkIf pkgs.stdenv.hostPlatform.isDarwin {
      launchd.agents.ai-memory = {
        enable = true;
        config = {
          ProgramArguments = [ (toString launcher) ];
          KeepAlive = {
            SuccessfulExit = false;
          };
          RunAtLoad = true;
          ProcessType = "Background";
          StandardOutPath = "${cfg.dataDir}/ai-memory.log";
          StandardErrorPath = "${cfg.dataDir}/ai-memory.log";
        };
      };
    })
  ]);
}
