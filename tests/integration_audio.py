"""Exercise the real sound server with silent virtual devices only."""

import asyncio
from array import array
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audio import AudioEngine, Preferences
from pulsectl_asyncio import PulseAsync


async def main():
    modules, processes = [], []
    with tempfile.TemporaryDirectory(prefix="sonora-test-") as temporary:
        folder = Path(temporary)
        engine = AudioEngine(lambda _: None, print, Preferences(folder / "prefs.json"))
        async with PulseAsync("Sonora-validation") as pulse:
            engine.pulse = pulse
            original = await pulse.server_info()
            try:
                for name in ("sonora_test_a", "sonora_test_b"):
                    module = await pulse.module_load("module-null-sink", f"sink_name={name} sink_properties=device.description={name}")
                    modules.append(module)
                    module = await pulse.module_load("module-remap-source", f"master={name}.monitor source_name={name}_mic source_properties=device.description={name}_mic")
                    modules.append(module)
                await asyncio.sleep(0.5)

                def play(name, amplitude):
                    filename = folder / (name + ".wav")
                    block = array("h", (round(amplitude * 32767 * math.sin(i * 2 * math.pi * 440 / 48000)) for i in range(48000)))
                    with wave.open(str(filename), "wb") as wav:
                        wav.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
                        for _ in range(60):
                            wav.writeframesraw(block.tobytes())
                    proc = subprocess.Popen(["paplay", "--device=sonora_test_a", "--client-name=" + name, str(filename)], stderr=subprocess.DEVNULL)
                    processes.append(proc)
                    return proc

                loud = play("Canal teste forte", 0.5)
                quiet = play("Canal teste baixo", 0.025)
                recording = subprocess.Popen(["parec", "--device=sonora_test_a_mic", "--client-name=Captura teste Sonora"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                processes.append(recording)
                await asyncio.sleep(0.5)
                await engine.refresh()
                a = next(x for x in engine.state["sink"] if x["name"] == "sonora_test_a")
                b = next(x for x in engine.state["sink"] if x["name"] == "sonora_test_b")
                mic_b = next(x for x in engine.state["source"] if x["name"] == "sonora_test_b_mic")
                strong = next(x for x in engine.state["playback"] if x["title"] == "Canal teste forte")
                weak = next(x for x in engine.state["playback"] if x["title"] == "Canal teste baixo")
                capture = next(x for x in engine.state["recording"] if x["title"] == "Captura teste Sonora")
                for item in (strong, weak):
                    await pulse.mute(engine.objects[item["key"]], False)
                    await pulse.volume_set_all_chans(engine.objects[item["key"]], 1)
                    await pulse.sink_input_move(item["index"], a["index"])
                await engine.refresh()
                await asyncio.sleep(1)
                strong_peak = engine.levels[strong["key"]][0]
                weak_peak = engine.levels[weak["key"]][0]
                assert strong_peak > weak_peak * 5, (strong_peak, weak_peak)
                print(f"PASS independent app peaks: {strong_peak:.4f} / {weak_peak:.4f}")
                await engine.execute("volume", "playback", strong["index"], 37)
                await engine.execute("mute", "playback", strong["index"], True)
                await engine.execute("route", "playback", strong["index"], b["name"])
                await engine.refresh()
                current = engine.item("playback", strong["index"])
                assert current["volume"] == 37 and current["mute"] and current["target"] == b["index"], current
                print("PASS playback volume, mute and move")
                await engine.execute("volume", "recording", capture["index"], 64)
                await engine.execute("mute", "recording", capture["index"], True)
                await engine.execute("route", "recording", capture["index"], mic_b["name"])
                await engine.refresh()
                current = engine.item("recording", capture["index"])
                assert current["volume"] == 64 and current["mute"] and current["target"] == mic_b["index"]
                print("PASS capture volume, mute and microphone move")
                await engine.execute("remember", "playback", strong["index"], True)
                saved = Preferences(folder / "prefs.json")
                assert saved.data["rules"]
                loud.terminate()
                loud.wait()
                await asyncio.sleep(0.2)
                await engine.refresh()
                play("Canal teste forte", 0.5)
                await asyncio.sleep(0.3)
                for _ in range(3):
                    await engine.refresh()
                    await asyncio.sleep(0.15)
                strong = next(x for x in engine.state["playback"] if x["title"] == "Canal teste forte")
                assert strong["volume"] == 37 and strong["mute"] and strong["target"] == b["index"], strong
                print("PASS persistent app rule after reopening")
                # Save only virtual devices in this fixture. Real devices remain untouched.
                await engine.execute("save_scene", "Teste")
                scene = engine.prefs.data["scenes"]["Teste"]
                scene["defaults"] = {}
                scene["devices"] = [d for d in scene["devices"] if d["name"].startswith("sonora_test_")]
                scene["apps"] = [s for s in scene["apps"] if s["identity"] == strong["identity"]]
                await engine.execute("volume", "playback", strong["index"], 12)
                await engine.execute("load_scene", "Teste")
                await engine.refresh()
                assert engine.item("playback", strong["index"])["volume"] == 37
                print("PASS scene save and restore")
                await engine.execute("visible", False)
                await engine.refresh()
                assert not engine.meters
                print("PASS hidden window releases all meters")
                after = await pulse.server_info()
                assert (original.default_sink_name, original.default_source_name) == (after.default_sink_name, after.default_source_name)
                print("PASS system defaults preserved")
            finally:
                await engine.clear_meters()
                for process in processes:
                    if process.poll() is None:
                        process.terminate()
                    process.wait(timeout=3)
                for module in reversed(modules):
                    await pulse.module_unload(module)


asyncio.run(main())
