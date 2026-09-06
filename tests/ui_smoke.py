"""Run inside agent-bench. Drive GTK widgets against silent virtual audio."""

from array import array
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pulsectl
from app import Sonora, GLib
from audio import Preferences

folder = tempfile.TemporaryDirectory(prefix="sonora-ui-")
path = Path(folder.name)
pulse = pulsectl.Pulse("Sonora-UI-validation")
modules = []
player = None
app = Sonora()
app.engine.prefs = Preferences(path / "preferences.json")
failed = []
context = {}


def channel():
    return next(w for w in app.window.channels.values() if w.item["title"] == "Teste visual Sonora")


def start():
    w = channel()
    w.scale.set_value(42)


def volume():
    assert channel().item["volume"] == 42, channel().item
    channel().mute.set_active(True)


def mute():
    assert channel().item["mute"]
    channel().route.set_selected(channel().route.values.index("sonora_ui_b"))


def route():
    destination = next(i for i in app.window.state["sink"] if i["name"] == "sonora_ui_b")
    assert channel().item["target"] == destination["index"]
    channel().remember.set_active(True)


def remember():
    assert app.window.state["rules"]
    app.window.stack.set_visible_child_name("preferences")
    assert app.window.rules.get_first_child() is not None
    app.window.boost_check.set_active(True)


def boost():
    assert app.window.max_volume == 150
    assert channel().scale.get_adjustment().get_upper() == 150
    app.window.stack.set_visible_child_name("scenes")
    app.window.scene_name.set_text("Cena de teste")
    app.window.save_scene()


def scene():
    assert "Cena de teste" in app.window.state["scenes"]
    app.window.stack.set_visible_child_name("devices")
    assert app.window.hardware.get_first_child() is not None
    app.window.hide_window()


def hidden():
    assert not app.window.is_visible()
    assert not app.engine.meters
    app.activate()


def reopened():
    assert app.window.is_visible()
    app.window.stack.set_visible_child_name("mixer")
    print("PASS GTK slider, mute, route, remember, boost, scenes, hardware tab, hide and reopen", flush=True)
    app.quit()


steps = iter([start, volume, mute, route, remember, boost, scene, hidden, reopened])


def step():
    try:
        task = next(steps)
        task()
    except StopIteration:
        return False
    except Exception as exc:
        import traceback
        traceback.print_exc()
        failed.append(exc)
        app.quit()
        return False
    return True


try:
    for name in ("sonora_ui_a", "sonora_ui_b"):
        modules.append(pulse.module_load("module-null-sink", f"sink_name={name} sink_properties=device.description={name}"))
    sample = path / "tone.wav"
    with wave.open(str(sample), "wb") as wav:
        wav.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        block = array("h", (round(6000 * math.sin(i * 2 * math.pi * 440 / 48000)) for i in range(48000)))
        for _ in range(30):
            wav.writeframesraw(block.tobytes())
    player = subprocess.Popen(["paplay", "--device=sonora_ui_a", "--client-name=Teste visual Sonora", str(sample)])
    GLib.timeout_add(1500, step)
    app.run(["sonora-ui-test"])
finally:
    if player:
        player.terminate()
        player.wait(timeout=3)
    for module in reversed(modules):
        pulse.module_unload(module)
    pulse.close()
    folder.cleanup()
if failed:
    raise SystemExit(1)
