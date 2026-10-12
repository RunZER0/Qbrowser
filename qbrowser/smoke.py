"""No-click model contract smoke test using a synthetic screenshot."""

from __future__ import annotations

import base64
import io

from qbrowser.native import CompatibleModel, ModelConfig, screen_message


def probe(model: object | None = None, config: ModelConfig | None = None) -> dict:
    from PIL import Image, ImageDraw

    config = config or ModelConfig.from_env()
    image = Image.new("RGB", (320, 200), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((105, 55, 215, 155), fill=(245, 20, 20))
    out = io.BytesIO()
    image.save(out, format="PNG")
    uri = "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()
    client = model or CompatibleModel(config)
    prompt = (
        "This is a simulated screen. Find the RED RECTANGLE near the middle. "
        "Use exactly one computer_action function call with action=click, "
        "x/y inside the red rectangle, impact=local, purpose='dry-run model smoke'. "
        "Do not execute the action; the test harness will inspect the function call."
    )
    history = [
        {"role": "system", "content": "You must use a function tool for this harmless visual test."},
        {"role": "user", "content": prompt},
        screen_message(uri, (320, 200)),
    ]
    response = client.ask(history)
    calls = response.get("calls") or []
    if len(calls) != 1:
        raise RuntimeError("Provider did not produce exactly one GUI tool call")
    call = calls[0]
    args = call["args"]
    if call["name"] != "computer_action" or args.get("action") != "click":
        raise RuntimeError("Provider failed screenshot-to-action tool calling")
    x, y = args.get("x"), args.get("y")
    if type(x) is not int or type(y) is not int or not (105 <= x <= 215 and 55 <= y <= 155):
        raise RuntimeError("Provider did not locate the highlighted UI target")
    return {"status": "passed", "provider": config.provider, "model": config.model,
            "coordinate": [x, y], "gui_executed": False}


def probe_desktop() -> dict:
    """Check real host screenshot and coordinate conversion without clicking anything."""
    try:
        from qbrowser.native import Desktop
        desk = Desktop()
        _image, (width, height) = desk.observe()
        center = desk.position({"x": width // 2, "y": height // 2})
        return {
            "status": "passed",
            "scaled_screenshot": [width, height],
            "desktop_resolution": list(desk.last_actual or (0, 0)),
            "logical_center": list(center),
            "gui_executed": False,
            "note": "Screenshot and coordinate mapping checked. Mouse/keyboard untested."
        }
    except Exception as exc:
        reason = ("No graphical display is available" if isinstance(exc, KeyError) and
                  exc.args == ("DISPLAY",) else
                  f"Desktop screenshot unavailable ({type(exc).__name__})")
        return {"status": "blocked", "error": reason, "gui_executed": False}
