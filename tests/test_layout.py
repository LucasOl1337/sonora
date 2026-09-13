"""Measure mixer rows in the GTK bench. No PipeWire, no human display."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk, Gdk, GLib

from app import COMPACT_WIDTH, ROOT, WINDOW_MIN_WIDTH, Window


class FakeEngine:
    def __init__(self):
        self.levels = {}
        self.commands = []

    def command(self, *args):
        self.commands.append(args)


def device(kind, index, name, title, default=False):
    return {
        "key": f"{kind}:{name}",
        "kind": kind,
        "name": name,
        "title": title,
        "subtitle": name,
        "index": index,
        "volume": 40,
        "mute": False,
        "default": default,
        "active": True,
        "ports": [{"name": "analog", "title": "Analógico"}],
        "port": "analog",
        "channels": 2,
        "direct_owner": None,
        "icon": "audio-speakers-symbolic",
        "target": 0,
        "following": False,
        "identity": name,
    }


def app_item(kind, index, title, target):
    return {
        "key": f"{kind}:{title}",
        "kind": kind,
        "name": title,
        "title": title,
        "subtitle": title,
        "index": index,
        "volume": 80,
        "mute": False,
        "default": False,
        "active": True,
        "ports": [],
        "port": None,
        "channels": 2,
        "icon": "audio-x-generic-symbolic",
        "target": target,
        "following": False,
        "identity": "application.name:" + title,
    }


STATE = {
    "connected": True,
    "boost": False,
    "sink": [device("sink", 1, "hdmi_long", "AD103 High Definition Audio Controller Digital Stereo (HDMI 3)", True)],
    "source": [device("source", 2, "usb_mic", "FIFINE USB Microphone Analog Stereo", True)],
    "missing_sinks": [{
        "key": "missing-out:card:hdmi-output-3",
        "kind": "missing_sink",
        "name": "card",
        "port": "hdmi-output-3",
        "title": "S85F",
        "index": 9,
        "profile": "output:hdmi-stereo-extra3",
    }],
    "missing_sources": [],
    "playback": [app_item("playback", 3, "Firefox Nightly", 1)],
    "recording": [],
    "defaults": {"sink": "hdmi_long", "source": "usb_mic"},
    "rules": {},
    "scenes": [],
    "cards": [],
    "microphone_test": None,
}


def min_width(widget):
    return widget.measure(Gtk.Orientation.HORIZONTAL, -1)[0]


class Harness(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="io.github.lol.Sonora.layout")
        self.engine = FakeEngine()
        self.failed = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_path(str(ROOT / "style.css"))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self):
        window = Window(self)
        window.update(STATE)
        window.present()
        GLib.idle_add(self.check, window)

    def check(self, window):
        try:
            verify(window)
        except Exception as exc:
            self.failed = exc
            import traceback
            traceback.print_exc()
        window.set_visible(False)
        self.quit()
        return False


def verify(window):
    sink = next(w for w in window.channels.values() if w.item["kind"] == "sink")
    source = next(w for w in window.channels.values() if w.item["kind"] == "source")
    playback = next(w for w in window.channels.values() if w.item["kind"] == "playback")
    missing = next(w for w in window.channels.values() if w.item["kind"] == "missing_sink")
    assert sink.test_btn.get_label() == "Testar"
    assert source.test_btn.get_label() == "Ouvir"
    assert playback.route is not None
    assert sink.scale.get_hexpand()
    assert window.get_size_request()[0] == WINDOW_MIN_WIDTH

    wide_sink = min_width(sink)
    wide_play = min_width(playback)
    wide_body = min_width(window.body)
    assert wide_sink <= 640, f"device row min {wide_sink} still wider than a 1/3 1080p tile"
    assert wide_play <= 640, f"app row min {wide_play} still wider than a 1/3 1080p tile"
    assert min_width(missing) <= 640, min_width(missing)

    window.adapt_to_width(COMPACT_WIDTH - 1)
    assert window.compact
    assert sink.inline_actions.get_parent() is sink.advanced
    assert playback.route.get_parent() is playback.route_box
    assert playback.more.get_visible()
    assert not window.output_caption.get_visible()
    compact_sink = min_width(sink)
    compact_play = min_width(playback)
    compact_body = min_width(window.body)
    assert compact_sink < wide_sink, (compact_sink, wide_sink)
    assert compact_play < wide_play, (compact_play, wide_play)
    assert compact_sink <= WINDOW_MIN_WIDTH, compact_sink
    assert compact_play <= WINDOW_MIN_WIDTH, compact_play
    assert compact_body <= WINDOW_MIN_WIDTH, compact_body

    window.adapt_to_width(COMPACT_WIDTH + 40)
    assert not window.compact
    assert sink.inline_actions.get_parent() is sink
    assert playback.route.get_parent() is playback
    assert sink.test_btn.get_label() == "Testar"

    # Numa tile alta, a sobra é absorvida pelo cartão de apps, não por um
    # vazio de fundo abaixo do conteúdo. A altura natural continua compacta.
    assert window.root.get_vexpand()
    assert window.scroll.get_vexpand()
    assert window.apps.get_vexpand()
    assert window.empty.get_parent() is window.apps
    # O monitor de atividade absorve a sobra e não custa altura mínima.
    assert window.wave.get_parent() is window.apps
    assert window.wave.get_vexpand()
    assert window.wave.measure(Gtk.Orientation.VERTICAL, -1)[0] == 0
    width = 840
    natural = window.measure(Gtk.Orientation.VERTICAL, width)[1]
    assert natural < 560, natural
    print(
        f"PASS layout wide min sink={wide_sink} app={wide_play} body={wide_body}; "
        f"compact min sink={compact_sink} app={compact_play} body={compact_body}; "
        f"natural {width}×{natural}",
        flush=True,
    )


if __name__ == "__main__":
    app = Harness()
    app.run(["sonora-layout-test"])
    if app.failed:
        raise SystemExit(1)
