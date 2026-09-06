"""Microphone discovery and selection against a private PipeWire server."""

import asyncio
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace as Obj
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audio import AudioEngine, Preferences, capture_pid, missing_microphones
from pulsectl_asyncio import PulseAsync


class DiscoveryTests(unittest.TestCase):
    def test_kernel_capture_status_accepts_aligned_field_names(self):
        self.assertEqual(capture_pid("state: RUNNING\nowner_pid   : 424864\ntrigger_time: 148015.48\n"), "424864")
        self.assertIsNone(capture_pid("closed\n"))

    def test_connected_microphone_is_visible_without_a_published_source(self):
        profile = Obj(name="input:mono-fallback", available=True, n_sources=1, n_sinks=0, priority=1)
        card = Obj(index=49, name="alsa_card.usb-FIFINE", profile_active=profile,
                   profile_list=[profile], proplist={"device.description": "FIFINE Microphone", "api.alsa.card": "4"})
        missing = missing_microphones([card], [])
        self.assertEqual([m["title"] for m in missing], ["FIFINE Microphone"])
        self.assertEqual(missing_microphones([card], [{"card": 49}]), [])

    def test_enabling_a_headset_microphone_preserves_its_output(self):
        playback = Obj(name="output:analog-stereo", available=True, n_sources=0, n_sinks=1, priority=6500)
        input_only = Obj(name="input:mono", available=True, n_sources=1, n_sinks=0, priority=9000)
        duplex = Obj(name="output:analog-stereo+input:mono", available=True, n_sources=1, n_sinks=1, priority=6000)
        card = Obj(index=51, name="headset", profile_active=playback, profile_list=[playback, input_only, duplex], proplist={})
        self.assertEqual(missing_microphones([card], [])[0]["profile"], duplex.name)


class SelectionTests(unittest.TestCase):
    def test_switch_default_microphone_on_private_server(self):
        with tempfile.TemporaryDirectory(prefix="sonora-microphones-") as folder:
            folder = Path(folder)
            config = folder / "pulse.conf"
            text = Path("/usr/share/pipewire/pipewire-pulse.conf").read_text()
            text = text.replace('"unix:native"', f'"unix:{folder}/pulse.sock"')
            text = text.replace('#server.dbus-name       = "org.pulseaudio.Server"', 'server.dbus-name = "org.sonora.AudioTest"')
            config.write_text(text)
            env = {**os.environ, "PIPEWIRE_RUNTIME_DIR": str(folder), "XDG_RUNTIME_DIR": str(folder), "XDG_STATE_HOME": str(folder / "state"), "XDG_CONFIG_HOME": str(folder / "config"), "PULSE_SERVER": f"unix:{folder}/pulse.sock"}
            processes = []
            old_server = os.environ.get("PULSE_SERVER")
            with (folder / "server.log").open("w+") as log:
                try:
                    processes.append(subprocess.Popen(["pipewire"], env=env, stdout=log, stderr=log))
                    time.sleep(.2)
                    processes.append(subprocess.Popen(["pipewire-pulse", "-c", str(config)], env=env, stdout=log, stderr=log))
                    for _ in range(30):
                        if subprocess.run(["pactl", "info"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                            break
                        time.sleep(.1)
                    processes.append(subprocess.Popen(["wireplumber", "--profile=policy"], env=env, stdout=log, stderr=log))
                    time.sleep(.4)
                    for args in (("module-null-sink", "sink_name=test"),
                                 ("module-remap-source", "master=test.monitor", "source_name=mic_a"),
                                 ("module-remap-source", "master=test.monitor", "source_name=mic_b")):
                        subprocess.run(["pactl", "load-module", *args], env=env, stdout=subprocess.DEVNULL, check=True, timeout=3)
                    time.sleep(.2)
                    os.environ["PULSE_SERVER"] = env["PULSE_SERVER"]
                    asyncio.run(self.exercise(folder))
                finally:
                    if old_server is None:
                        os.environ.pop("PULSE_SERVER", None)
                    else:
                        os.environ["PULSE_SERVER"] = old_server
                    for process in reversed(processes):
                        process.terminate()
                        process.wait(timeout=3)

    async def exercise(self, folder):
        engine = AudioEngine(lambda _: None, self.fail, Preferences(folder / "prefs.json"))
        engine.visible = False
        async with PulseAsync("Sonora-test") as engine.pulse:
            await engine.refresh()
            for name in ("mic_a", "mic_b", "mic_a"):
                await engine.execute("microphone", name)
                for _ in range(20):
                    await asyncio.sleep(.05)
                    await engine.refresh()
                    if engine.state["defaults"]["source"] == name:
                        break
                self.assertEqual(engine.state["defaults"]["source"], name)
                self.assertEqual([s["name"] for s in engine.state["source"] if s["default"]], [name])


if __name__ == "__main__":
    unittest.main()
