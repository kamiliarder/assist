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
        ordis-core = pkgs.writeShellApplication rec {
          name = "ordis-core";
          runtimeInputs = [ pyCore ];
          text = ''
            exec ${pyCore}/bin/python /home/qmil/Projects/assist/ordis-core "$@"
          '';
        };

        ordis = pkgs.writeShellApplication rec {
          name = "ordis";
          runtimeInputs = [ pyGui pkgs.gtk4 pkgs.gtk4-layer-shell pkgs.glib pkgs.playerctl ];
          text = ''
            export GTK4_LAYER_SHELL_LIB=${pkgs.gtk4-layer-shell}/lib/libgtk4-layer-shell.so
            exec ${pyGui}/bin/python /home/qmil/Projects/assist/assistant.py "$@"
          '';
        };
      };

      devShells.${system}.default = pkgs.mkShell {
        # gobject-introspection's setup hook fills GI_TYPELIB_PATH from buildInputs
        nativeBuildInputs = [ pkgs.gobject-introspection ];

        buildInputs = [
          pyGui
          pyGui.pkgs.python-lsp-server
          pyGui.pkgs.python-lsp-ruff  # Fast linting and formatting
          pyGui.pkgs.pylsp-mypy       # Type checking
          pyGui.pkgs.pylsp-rope       # Refactoring support
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
