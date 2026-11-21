"""UI layer factory."""

def create_ui(ui_type="gtk"):
    if ui_type == "gtk":
        from .gtk.app import App
        return App
    raise ValueError(f"unknown UI type: {ui_type}")
