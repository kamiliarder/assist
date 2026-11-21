{
  description = "ordis: a layer-shell AI assistant for niri, backed by any OpenAI-compatible provider";

  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};

      pyCore = pkgs.python3.withPackages (ps: [
        ps.httpx # streaming HTTP to the API
      ]);

      pyGui = pkgs.python3.withPackages (ps: [
        ps.pygobject3 # GTK4 and layer-shell bindings
        ps.httpx # streaming HTTP to the API
      ]);
    in
    {
      packages.${system} = {
        ordis-core = pkgs.writeShellScriptBin "ordis-core" ''
          export PYTHONPATH="${./ordis}:${PYTHONPATH:-}"
          exec ${pyCore}/bin/python "${./ordis-core}" "$@"
        '';

        ordis = pkgs.writeShellScriptBin "ordis" ''
          export GTK4_LAYER_SHELL_LIB=${pkgs.gtk4-layer-shell}/lib/libgtk4-layer-shell.so
          export PYTHONPATH="${./ordis}:${PYTHONPATH:-}"
          exec ${pyGui}/bin/python "${./assistant.py}" "$@"
        '';
      };

      devShells.${system}.default = pkgs.mkShell {
        nativeBuildInputs = [ pkgs.gobject-introspection ];

        buildInputs = [
          pyGui
          pyGui.pkgs.python-lsp-server
          pyGui.pkgs.python-lsp-ruff
          pyGui.pkgs.pylsp-mypy   
          pyGui.pkgs.pylsp-rope       
          pkgs.gtk4
          pkgs.gtk4-layer-shell
          pkgs.glib
          pkgs.playerctl
        ];

        shellHook = ''
          # gtk4-layer-shell must load before libwayland-client;
          # assistant.py re-execs itself with this preloaded
          export GTK4_LAYER_SHELL_LIB=${pkgs.gtk4-layer-shell}/lib/libgtk4-layer-shell.so
        '';
      };
    };
}
