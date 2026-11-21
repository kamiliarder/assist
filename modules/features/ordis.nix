{ config, lib, pkgs, ... }:

with lib;

let
  cfg = config.services.ordis;
in
{
  options.services.ordis = {
    enable = mkEnableOption "Enable ordis AI assistant";

    package = mkOption {
      type = types.package;
      default = pkgs.ordis;
      description = "ordis package to install";
    };

    apiKeyFile = mkOption {
      type = types.str;
      description = ''
        Path to the NVIDIA/OpenAI API key file.
        Typically provided by sops-nix or agenix (e.g. /run/keys/ordis-api-key).
      '';
      default = "/run/keys/ordis-api-key";
    };

    cssFile = mkOption {
      type = types.str;
      description = ''
        Path to user GTK CSS file (e.g. ~/.config/gtk-4.0/noctalia.css).
        Loaded at STYLE_PROVIDER_PRIORITY_USER so it can override built-in styles.
      '';
      default = "${config.home.username}/.config/gtk-4.0/noctalia.css";
    };

    niriBind = mkOption {
      type = types.bool;
      description = ''
        Add a Mod+A keybind in niri to launch ordis.
      '';
      default = true;
    };
  };

  config = mkIf cfg.enable {
    environment.systemPackages = [ cfg.package ];

    # systemd user service — launches ordis on login
    systemd.user.services.ordis = {
      description = "ordis AI assistant";
      wantedBy = ["default.target"];
      serviceConfig = {
        ExecStart = "${cfg.package}/bin/assistant.py";
        Environment = "ORDIS_API_KEY_FILE=${cfg.apiKeyFile}";
        Environment = "ORDIS_CSS_FILE=${cfg.cssFile}";
        Restart = "on-failure";
      };
    };

    # niri keybind: Mod+A launches ordis
    niri.config = mkIf cfg.niriBind {
      keybinds = [
        {
          modifier = [ "Mod" ];
          key = "A";
          action = "spawn";
          command = "${cfg.package}/bin/assistant.py";
        }
      ];
    };
  };
}
