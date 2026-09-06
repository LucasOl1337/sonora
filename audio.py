"""PipeWire/PulseAudio control and live peaks, confined to one asyncio thread."""

import asyncio
import copy
import json
import logging
import os
from pathlib import Path
import threading
import time

from pulsectl_asyncio import PulseAsync

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "sonora" / "preferences.json"
KINDS = {"sink": "sink_list", "source": "source_list", "playback": "sink_input_list", "recording": "source_output_list"}


def identity(props):
    if props.get("application.id"):
        return "application.id:" + props["application.id"]
    if props.get("application.process.binary"):
        return "application:" + props["application.process.binary"] + ":" + props.get("application.name", "")
    if props.get("application.name"):
        return "application.name:" + props["application.name"]
    return "media.name:" + props.get("media.name", "unknown")


class Preferences:
    def __init__(self, path=CONFIG):
        self.path = path
        self.data = {"rules": {}, "scenes": {}}
        self.error = None
        if path.exists():
            try:
                data = json.loads(path.read_text())
                if not isinstance(data, dict) or not all(isinstance(data.get(k, {}), dict) for k in ("rules", "scenes")):
                    raise ValueError("formato inválido")
                self.data.update(data)
            except (ValueError, OSError) as exc:
                self.error = f"Não foi possível ler preferências: {exc}"

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n")
        temp.replace(self.path)


class AudioEngine:
    def __init__(self, on_state, on_error, preferences=None):
        self.on_state, self.on_error = on_state, on_error
        self.prefs = preferences or Preferences()
        self.levels = {}
        self.state = {}
        self.objects = {}
        self.meters = {}
        self.seen = set()
        self.transient_routes = {}
        self.visible = True
        self.running = True
        self.loop = None
        self.commands = None
        self.thread = threading.Thread(target=self._thread, daemon=True, name="sonora-audio")

    def start(self):
        self.thread.start()

    def stop(self):
        self.running = False
        if self.loop and self.commands:
            self.loop.call_soon_threadsafe(self.commands.put_nowait, ("wake", ()))
        self.thread.join(timeout=3)

    def command(self, action, *args):
        if self.loop and self.commands:
            self.loop.call_soon_threadsafe(self.commands.put_nowait, (action, args))

    def _thread(self):
        asyncio.run(self._run())

    async def _run(self):
        self.loop = asyncio.get_running_loop()
        self.commands = asyncio.Queue()
        if self.prefs.error:
            self.on_error(self.prefs.error)
        while self.running:
            try:
                async with PulseAsync("Sonora") as self.pulse:
                    self.seen.clear()
                    while self.running:
                        await self.refresh()
                        try:
                            action, args = await asyncio.wait_for(self.commands.get(), timeout=0.7)
                        except asyncio.TimeoutError:
                            continue
                        pending = [(action, args)]
                        while not self.commands.empty():
                            pending.append(self.commands.get_nowait())
                        # Slider motion may queue many values. Keep the last per control.
                        latest = {}
                        for action, args in pending:
                            key = (action, *args[:2]) if action in ("volume", "mute") else (len(latest),)
                            latest[key] = (action, args)
                        for action, args in latest.values():
                            try:
                                await self.execute(action, *args)
                            except Exception as exc:
                                logging.exception("Audio command %s", action)
                                self.on_error(f"Não foi possível aplicar a mudança: {exc}")
            except Exception as exc:
                logging.exception("Audio connection")
                self.on_error(f"Áudio desconectado. Tentando reconectar… {exc}")
                self.on_state({"connected": False})
                await asyncio.sleep(2)
            finally:
                await self.clear_meters()

    async def clear_meters(self):
        tasks = [entry[1] for entry in self.meters.values()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.meters.clear()
        self.levels.clear()

    async def peak(self, key, source, stream=None):
        try:
            async for level in self.pulse.subscribe_peak_sample(source, rate=30, stream_idx=stream, allow_suspend=key.startswith(("sink:", "playback:"))):
                self.levels[key] = (max(0, min(1, level)), time.monotonic())
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.debug("Meter unavailable: %s", key, exc_info=True)
        finally:
            self.levels.pop(key, None)

    async def refresh(self):
        server = await self.pulse.server_info()
        groups = {}
        self.objects = {}
        for kind, method in KINDS.items():
            objects = await getattr(self.pulse, method)()
            groups[kind] = []
            for obj in objects:
                props = obj.proplist
                if props.get("application.name", "").startswith("Sonora"):
                    continue
                if kind == "source" and obj.monitor_of_sink != 4294967295:
                    continue
                key = f"{kind}:{obj.index}"
                self.objects[key] = obj
                device = kind in ("sink", "source")
                default = server.default_sink_name if kind == "sink" else server.default_source_name
                entry = {
                    "key": key, "kind": kind, "index": obj.index,
                    "name": obj.name, "title": obj.description if device else props.get("application.name", obj.name),
                    "subtitle": obj.name if device else props.get("media.name", obj.name),
                    "volume": round(obj.volume.value_flat * 100), "mute": bool(obj.mute),
                    "channels": len(obj.volume.values),
                    "identity": identity(props), "default": device and obj.name == default,
                    "icon": props.get("application.icon_name", "audio-card-symbolic" if device else "applications-multimedia-symbolic"),
                    "active": getattr(obj, "state", None) == "running" if device else not bool(obj.corked),
                    "target": getattr(obj, "sink" if kind == "playback" else "source", None) if not device else None,
                    "following": key in self.transient_routes and self.transient_routes[key] is None,
                    "ports": [{"name": p.name, "title": p.description, "available": str(p.available)} for p in getattr(obj, "port_list", [])],
                    "port": getattr(getattr(obj, "port_active", None), "name", None),
                }
                if hasattr(obj, "monitor_source_name"):
                    entry["monitor"] = obj.monitor_source_name
                groups[kind].append(entry)
        for device_kind, stream_kind in (("sink", "playback"), ("source", "recording")):
            for item in groups[device_kind]:
                item["active"] = any(s["target"] == item["index"] and s["active"] for s in groups[stream_kind])
        cards = await self.pulse.card_list()
        for card in cards:
            self.objects[f"card:{card.index}"] = card
        state = {"connected": True, **groups, "defaults": {"sink": server.default_sink_name, "source": server.default_source_name},
                 "cards": [{"index": c.index, "title": c.proplist.get("device.description", c.name),
                            "active": c.profile_active.name if c.profile_active else "",
                            "profiles": [{"name": p.name, "title": p.description} for p in c.profile_list if p.available]} for c in cards]}
        await self.apply_rules(state)
        state["rules"] = copy.deepcopy(self.prefs.data["rules"])
        state["scenes"] = list(self.prefs.data["scenes"])
        state["boost"] = bool(self.prefs.data.get("boost", False))
        self.state = state
        await self.sync_meters(state)
        self.on_state(state)

    async def sync_meters(self, state):
        wanted = {}
        if self.visible:
            for item in state["sink"]:
                wanted[item["key"]] = (item["monitor"], None)
            # Only monitor microphones that are already being used, or the default.
            for item in state["source"]:
                if item["active"] or item["default"]:
                    wanted[item["key"]] = (item["name"], None)
            sinks = {item["index"]: item for item in state["sink"]}
            for item in state["playback"]:
                if item["target"] in sinks:
                    wanted[item["key"]] = (sinks[item["target"]]["monitor"], item["index"])
        for key in list(self.meters):
            spec, task = self.meters[key]
            if wanted.get(key) != spec or task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                del self.meters[key]
        for key, spec in wanted.items():
            if key not in self.meters:
                self.meters[key] = (spec, asyncio.create_task(self.peak(key, *spec)))

    async def apply_rules(self, state):
        current = set()
        for kind, target_kind in (("playback", "sink"), ("recording", "source")):
            devices = {x["name"]: x for x in state[target_kind]}
            for item in state[kind]:
                key = item["key"]
                current.add(key)
                rule = self.prefs.data["rules"].get(kind + ":" + item["identity"])
                if not isinstance(rule, dict) and key not in self.transient_routes:
                    continue
                rule = rule or {}
                target_name = self.transient_routes.get(key, rule.get("target")) or state["defaults"][target_kind]
                target = devices.get(target_name)
                if target and item["target"] != target["index"]:
                    await self.move(item, target["index"])
                if rule and key not in self.seen:
                    await self.pulse.volume_set_all_chans(self.objects[key], max(0, min(1.5, rule.get("volume", 100) / 100)))
                    await self.pulse.mute(self.objects[key], bool(rule.get("mute", False)))
        self.seen = current
        self.transient_routes = {k: v for k, v in self.transient_routes.items() if k in current}

    async def move(self, item, target):
        method = self.pulse.sink_input_move if item["kind"] == "playback" else self.pulse.source_output_move
        await method(item["index"], target)

    def item(self, kind, index):
        return next(x for x in self.state[kind] if x["index"] == index)

    def remember_change(self, item, **changes):
        key = item["kind"] + ":" + item["identity"]
        if key in self.prefs.data["rules"]:
            self.prefs.data["rules"][key].update(changes)
            self.prefs.save()

    async def execute(self, action, *args):
        if action == "wake":
            return
        if action == "visible":
            self.visible = args[0]
            return
        if action == "boost":
            self.prefs.data["boost"] = bool(args[0])
            self.prefs.save()
            return
        if action == "forget":
            self.prefs.data["rules"].pop(args[0], None)
            self.prefs.save()
            return
        if action == "delete_scene":
            self.prefs.data["scenes"].pop(args[0], None)
            self.prefs.save()
            return
        if action == "save_scene":
            name = args[0].strip()[:60]
            if not name:
                raise ValueError("Dê um nome à cena.")
            await self.refresh()
            scene = {"defaults": self.state["defaults"], "devices": [], "apps": []}
            for kind in KINDS:
                for item in self.state[kind]:
                    saved = {k: item[k] for k in ("kind", "name", "identity", "volume", "mute", "title")}
                    if kind in ("sink", "source"):
                        scene["devices"].append(saved)
                    else:
                        targets = self.state["sink" if kind == "playback" else "source"]
                        saved["target"] = next((t["name"] for t in targets if t["index"] == item["target"]), None)
                        scene["apps"].append(saved)
            self.prefs.data["scenes"][name] = scene
            self.prefs.save()
            return
        if action == "load_scene":
            scene = self.prefs.data["scenes"][args[0]]
            for kind, name in scene["defaults"].items():
                for item in self.state[kind]:
                    if item["name"] == name:
                        await self.pulse.default_set(self.objects[item["key"]])
            for saved in scene["devices"] + scene["apps"]:
                for item in self.state[saved["kind"]]:
                    match = item["name"] == saved["name"] if saved["kind"] in ("sink", "source") else item["identity"] == saved["identity"]
                    if match:
                        obj = self.objects[item["key"]]
                        await self.pulse.volume_set_all_chans(obj, saved["volume"] / 100)
                        await self.pulse.mute(obj, saved["mute"])
                        if "target" in saved:
                            targets = self.state["sink" if saved["kind"] == "playback" else "source"]
                            for target in targets:
                                if target["name"] == saved["target"]:
                                    await self.move(item, target["index"])
                            self.remember_change(item, volume=saved["volume"], mute=saved["mute"], target=saved["target"])
            return
        if action == "profile":
            await self.pulse.card_profile_set(self.objects[f"card:{args[0]}"], args[1])
            return
        kind, index = args[:2]
        item = self.item(kind, index)
        obj = self.objects[item["key"]]
        if action == "volume":
            value = max(0, min(150, args[2]))
            await self.pulse.volume_set_all_chans(obj, value / 100)
            self.remember_change(item, volume=value)
        elif action == "mute":
            await self.pulse.mute(obj, args[2])
            self.remember_change(item, mute=args[2])
        elif action == "default":
            await self.pulse.default_set(obj)
        elif action == "route":
            target_kind = "sink" if kind == "playback" else "source"
            name = args[2] or self.state["defaults"][target_kind]
            target = next(t for t in self.state[target_kind] if t["name"] == name)
            await self.move(item, target["index"])
            self.transient_routes[item["key"]] = args[2]
            self.remember_change(item, target=args[2])
        elif action == "remember":
            key = kind + ":" + item["identity"]
            if args[2]:
                obj = await (self.pulse.sink_input_info(index) if kind == "playback" else self.pulse.source_output_info(index))
                targets = self.state["sink" if kind == "playback" else "source"]
                target_index = obj.sink if kind == "playback" else obj.source
                target = next((x["name"] for x in targets if x["index"] == target_index), None)
                target = self.transient_routes.get(item["key"], target)
                self.prefs.data["rules"][key] = {"title": item["title"], "kind": kind, "target": target, "volume": round(obj.volume.value_flat * 100), "mute": bool(obj.mute)}
            else:
                self.prefs.data["rules"].pop(key, None)
            self.prefs.save()
        elif action == "port":
            await self.pulse.port_set(obj, args[2])
        elif action == "balance":
            if len(obj.volume.values) == 2:
                base = max(obj.volume.values)
                balance = args[2]
                obj.volume.values = [base * (1 - max(0, balance)), base * (1 + min(0, balance))]
                await self.pulse.volume_set(obj, obj.volume)
