{
  description = "ordis: a layer-shell AI assistant for niri, backed by any OpenAI-compatible provider";

  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
      let
        system = "x86_64-linux";
        pkgs = nixpkgs.legacyPackages.${system};
        lib = pkgs.lib;

        py = pkgs.python3.withPackages (ps: [
          ps.pygobject3 # GTK4 bindings (the core's app launcher imports gi too)
          ps.httpx
          ps.faster-whisper # speech-to-text (local, runs on CPU)
          ps.sounddevice # microphone recording
          ps.numpy # audio arrays for faster-whisper
        ]);

        # Everything goes into the store together, so `ordis/` sits next to the
        # entry scripts and `import ordis` works. Only these paths: .env and
        # other stray files stay out of the world-readable store.
        src = lib.fileset.toSource {
          root = ./.;
          fileset = lib.fileset.unions [
            ./assistant.py
            ./ordis-core
            ./ordis
          ];
        };

        ordis = pkgs.stdenv.mkDerivation {
          pname = "ordis";
          version = "0.1.0";
          inherit src;

          nativeBuildInputs = [
            pkgs.makeWrapper
            pkgs.wrapGAppsHook4
            pkgs.gobject-introspection
          ];
          buildInputs = [
            pkgs.gtk4
            pkgs.gtk4-layer-shell
            pkgs.glib
            pkgs.pa # PortAudio for sounddevice microphone recording
          ];

          dontConfigure = true;
          dontBuild = true;

          installPhase = ''
            runHook preInstall
            mkdir -p $out/lib/ordis $out/bin
            cp -r assistant.py ordis-core ordis $out/lib/ordis/
            makeWrapper ${py}/bin/python $out/bin/ordis \
              --add-flags $out/lib/ordis/assistant.py
            makeWrapper ${py}/bin/python $out/bin/ordis-core \
              --add-flags $out/lib/ordis/ordis-core
            runHook postInstall
          '';

          # wrapGAppsHook4 adds the GTK typelibs; these are the extras
          preFixup = ''
            gappsWrapperArgs+=(
              --set GTK4_LAYER_SHELL_LIB ${pkgs.gtk4-layer-shell}/lib/libgtk4-layer-shell.so
              --prefix PATH : ${lib.makeBinPath [ pkgs.playerctl ]}
            )
          '';
        };
      in
      {
        packages.${system} = {
          default = ordis;
          inherit ordis;
          ordis-core = ordis; # one package, two programs: bin/ordis and bin/ordis-core
        };

      devShells.${system}.default = pkgs.mkShell {
        nativeBuildInputs = [ pkgs.gobject-introspection ];

        buildInputs = [
          py
          py.pkgs.python-lsp-server
          py.pkgs.python-lsp-ruff
          py.pkgs.pylsp-mypy
          py.pkgs.pylsp-rope
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
