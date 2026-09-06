#!/usr/bin/env python3
"""Sonora, a native desktop mixer with Sussurro's graphite/orange palette."""

import json
import logging
import math
import os
from pathlib import Path
import signal
import sys
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_foreign("cairo")
from gi.repository import Gtk, Gdk, Gio, GLib, GLibUnix, Pango

from audio import AudioEngine

ROOT = Path(__file__).resolve().parent
AUTOSTART = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart" / "sonora.desktop"


def box(vertical=False, spacing=10, css=None):
    widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL, spacing=spacing)
    if css:
        widget.add_css_class(css)
    return widget


def label(text="", css=None, expand=False):
    widget = Gtk.Label(label=text, xalign=0)
    widget.set_ellipsize(Pango.EllipsizeMode.END)
    widget.set_hexpand(expand)
    if css:
        for name in css.split():
            widget.add_css_class(name)
    return widget


def button(text, callback, css=None):
    widget = Gtk.Button(label=text)
    widget.connect("clicked", lambda _: callback())
    if css:
        widget.add_css_class(css)
    return widget


def clear(widget):
    child = widget.get_first_child()
    while child:
        nxt = child.get_next_sibling()
        widget.remove(child)
        child = nxt


class Choice(Gtk.DropDown):
    def __init__(self, callback, tooltip):
        super().__init__()
        self.values = []
        self.labels = []
        self.updating = False
        self.callback = callback
        self.set_tooltip_text(tooltip)
        self.set_hexpand(True)
        self.set_enable_search(True)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self.setup_label)
        factory.connect("bind", lambda _, item: item.get_child().set_label(item.get_item().get_string()))
        self.set_factory(factory)
        self.connect("notify::selected", self.changed)

    def setup_label(self, _, item):
        text = label()
        text.set_max_width_chars(18)
        item.set_child(text)

    def update(self, options, selected):
        self.updating = True
        values, names = [x[0] for x in options], [x[1] for x in options]
        if values != self.values or names != self.labels:
            self.values, self.labels = values, names
            self.set_model(Gtk.StringList.new(names))
        self.set_selected(values.index(selected) if selected in values else Gtk.INVALID_LIST_POSITION)
        self.set_sensitive(bool(values))
        self.updating = False

    def changed(self, *_):
        index = self.get_selected()
        if not self.updating and index < len(self.values):
            self.callback(self.values[index])


class Meter(Gtk.DrawingArea):
    def __init__(self, engine):
        super().__init__()
        self.engine, self.key = engine, None
        self.display = 0.0
        self.muted = False
        self.set_content_width(100)
        self.set_content_height(3)
        self.set_draw_func(self.draw)
        self.set_tooltip_text("Nível real do áudio, de −60 a 0 dBFS")

    def draw(self, _, ctx, width, height):
        sample, timestamp = self.engine.levels.get(self.key, (0, 0))
        value = max(0, (20 * math.log10(max(sample, 0.001)) + 60) / 60) if time.monotonic() - timestamp < 0.3 and not self.muted else 0
        self.display = max(value, self.display * 0.83)
        ctx.set_source_rgb(0.19, 0.20, 0.23)
        ctx.rectangle(0, 0, width, height)
        ctx.fill()
        ctx.set_source_rgb(*( (0.96, 0.28, 0.25) if self.display > 0.97 else (0.95, 0.39, 0.16)))
        ctx.rectangle(0, 0, width * self.display, height)
        ctx.fill()


def short_device(name):
    if "High Definition Audio Controller" in name and "(HDMI" in name:
        return name.rsplit("(", 1)[1].rstrip(")") + " · " + name.split(" High Definition", 1)[0]
    return name.replace(" Analog Stereo", "").replace(" Mono", "").replace(" Digital Stereo", "")


def icon_button(icon, tooltip, callback=None, toggle=False):
    widget = Gtk.ToggleButton() if toggle else Gtk.Button()
    widget.set_icon_name(icon)
    widget.set_tooltip_text(tooltip)
    widget.add_css_class("icon-button")
    widget.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
    if callback:
        widget.connect("clicked", lambda _: callback())
    return widget


class Channel(Gtk.Box):
    def __init__(self, window, item):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.add_css_class("channel")
        self.set_valign(Gtk.Align.START)
        self.window, self.item = window, item
        self.updating = False
        self.edit_until = 0
        self.pending = None
        self.volume_timer = None
        self.device = item["kind"] in ("sink", "source")
        is_input = item["kind"] in ("source", "recording")
        self.icon = Gtk.Image.new_from_icon_name("audio-input-microphone-symbolic" if is_input else ("audio-speakers-symbolic" if self.device else item["icon"]))
        self.icon.set_pixel_size(16)
        self.icon.set_size_request(18, -1)
        self.icon.set_tooltip_text("Entrada de áudio" if is_input else "Saída de áudio")
        self.append(self.icon)
        self.title = label("", "channel-name", True)
        self.title.set_size_request(180, -1)
        self.title.set_max_width_chars(24)
        self.append(self.title)
        self.mute = icon_button("audio-volume-high-symbolic", "Silenciar " + item["title"], toggle=True)
        self.mute.connect("toggled", self.on_mute)
        self.append(self.mute)
        levels = box(True, 0)
        levels.set_valign(Gtk.Align.CENTER)
        levels.set_size_request(136, -1)
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, window.max_volume, 1)
        self.scale.set_draw_value(False)
        self.scale.set_hexpand(True)
        self.scale.set_tooltip_text("Volume de " + item["title"])
        self.scale.connect("value-changed", self.on_volume)
        levels.append(self.scale)
        self.meter = Meter(window.engine)
        self.meter.key = item["key"]
        levels.append(self.meter)
        self.append(levels)
        self.readout = label("", "mono small")
        self.readout.set_xalign(1)
        self.readout.set_size_request(40, -1)
        self.append(self.readout)
        self.route = None
        self.remember = None
        self.default_btn = None
        if self.device:
            destination = box(spacing=8)
            destination.set_size_request(174, -1)
            destination.set_valign(Gtk.Align.CENTER)
            self.default_btn = button("Usar", lambda: self.send("default"))
            self.default_btn.set_size_request(62, -1)
            self.default_btn.set_tooltip_text("Usar como microfone padrão" if is_input else "Usar como saída padrão")
            destination.append(self.default_btn)
            self.activity = label("", "muted small")
            destination.append(self.activity)
            self.append(destination)
            self.more = Gtk.MenuButton(icon_name="view-more-symbolic")
            self.more.add_css_class("icon-button")
            self.more.set_tooltip_text("Conectores e balanço")
            self.device_popover = Gtk.Popover()
            advanced = box(True, 10)
            for side in ("top", "bottom", "start", "end"):
                getattr(advanced, "set_margin_" + side)(8)
            advanced.append(label(item["title"], "channel-name"))
            self.ports = Choice(lambda name: self.send("port", name), "Conector do dispositivo")
            advanced.append(self.ports)
            if item["channels"] == 2:
                advanced.append(label("Balanço esquerdo / direito", "small muted"))
                balance = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, -1, 1, 0.05)
                balance.set_size_request(220, -1)
                balance.set_draw_value(False)
                balance.add_mark(0, Gtk.PositionType.BOTTOM, "Centro")
                balance.connect("value-changed", lambda w: self.send("balance", w.get_value()))
                advanced.append(balance)
            self.device_popover.set_child(advanced)
            self.more.set_popover(self.device_popover)
            self.append(self.more)
        else:
            self.route = Choice(lambda name: self.send("route", name), "Microfone deste app" if is_input else "Saída deste app")
            self.route.set_hexpand(False)
            self.route.set_size_request(174, -1)
            self.route.set_valign(Gtk.Align.CENTER)
            self.append(self.route)
            self.remember = icon_button("non-starred-symbolic", "Lembrar dispositivo, volume e mudo para este app", toggle=True)
            self.remember.connect("toggled", self.on_remember)
            self.append(self.remember)
        self.update(item, window.state)

    def send(self, action, *args):
        self.window.engine.command(action, self.item["kind"], self.item["index"], *args)

    def on_volume(self, *_):
        if self.updating:
            return
        self.pending = round(self.scale.get_value())
        self.readout.set_text(f"{self.pending}%")
        self.edit_until = time.monotonic() + 1
        if not self.volume_timer:
            self.volume_timer = GLib.timeout_add(60, self.flush_volume)

    def flush_volume(self):
        self.send("volume", self.pending)
        self.volume_timer = None
        return False

    def on_mute(self, *_):
        if not self.updating:
            self.send("mute", self.mute.get_active())

    def on_remember(self, *_):
        if not self.updating:
            self.send("remember", self.remember.get_active())

    def update(self, item, state):
        self.updating = True
        self.item = item
        title = short_device(item["title"]) if self.device else item["title"]
        if title.startswith("qemu-system-"):
            title = "Máquina virtual"
        self.title.set_text(title)
        self.title.set_tooltip_text(item["title"] + "\n" + item["subtitle"])
        self.scale.set_range(0, max(self.window.max_volume, item["volume"]))
        if time.monotonic() > self.edit_until:
            self.scale.set_value(item["volume"])
            self.readout.set_text(f'{item["volume"]}%')
        is_input = item["kind"] in ("source", "recording")
        self.mute.set_active(item["mute"])
        self.mute.set_icon_name(("microphone-disabled-symbolic" if is_input else "audio-volume-muted-symbolic") if item["mute"] else ("microphone-sensitivity-high-symbolic" if is_input else "audio-volume-high-symbolic"))
        self.mute.set_tooltip_text(("Reativar " if item["mute"] else "Silenciar ") + title)
        self.meter.muted = item["mute"]
        if item["kind"] == "recording":
            self.meter.key = f'source:{item["target"]}'
            self.meter.set_tooltip_text("Nível do microfone usado por este app")
        if self.route:
            devices = state["sink" if item["kind"] == "playback" else "source"]
            rule = state["rules"].get(item["kind"] + ":" + item["identity"])
            target = next((d["name"] for d in devices if d["index"] == item["target"]), None)
            options = [(None, "Padrão do sistema")] + [(d["name"], short_device(d["title"])) for d in devices]
            if item["following"] or (rule and rule.get("target") is None):
                target = None
            self.route.update(options, target)
            self.remember.set_active(bool(rule))
            self.remember.set_icon_name("starred-symbolic" if rule else "non-starred-symbolic")
        else:
            self.default_btn.set_label("Padrão" if item["default"] else "Usar")
            self.default_btn.set_sensitive(not item["default"])
            if item["default"]:
                self.default_btn.add_css_class("default-device")
            else:
                self.default_btn.remove_css_class("default-device")
            self.activity.set_text("Em uso" if item["active"] else "")
            self.ports.update([(p["name"], p["title"]) for p in item["ports"]], item["port"])
            self.ports.set_visible(bool(item["ports"]))
            self.more.set_sensitive(item["channels"] == 2 or bool(item["ports"]))
        self.updating = False


class Window(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Sonora")
        self.set_default_size(780, -1)
        self.app, self.engine = app, app.engine
        self.state = {}
        self.max_volume = 100
        self.channels = {}
        self.signature = None
        self.settings_window = None
        self.connect("close-request", self.hide_window)
        header = Gtk.HeaderBar()
        header.set_decoration_layout(":close")
        title = box(spacing=7)
        icon = Gtk.Image.new_from_file(str(ROOT / "assets" / "sonora.svg"))
        icon.set_pixel_size(21)
        title.append(icon)
        title.append(label("SONORA", "brand"))
        header.set_title_widget(title)
        header.pack_end(icon_button("emblem-system-symbolic", "Opções do Sonora", self.open_settings))
        self.set_titlebar(header)
        root = box(True, 8)
        self.root = root
        root.set_valign(Gtk.Align.START)
        for side in ("top", "bottom", "start", "end"):
            getattr(root, "set_margin_" + side)(12)
        # Center the natural-width list if the user expands the window.
        centered = Gtk.CenterBox()
        centered.set_center_widget(root)
        self.set_child(centered)
        self.error = box(spacing=6, css="error")
        self.error_label = label("", expand=True)
        self.error_label.set_wrap(True)
        self.error.append(self.error_label)
        self.error.append(icon_button("window-close-symbolic", "Fechar aviso", lambda: self.error.set_visible(False)))
        self.error.set_visible(False)
        root.append(self.error)
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_propagate_natural_height(True)
        monitor = Gdk.Display.get_default().get_monitors().get_item(0)
        self.scroll.set_max_content_height(max(260, monitor.get_geometry().height - 160))
        self.body = box(True, 6)
        self.scroll.set_child(self.body)
        root.append(self.scroll)
        self.body.append(label("DISPOSITIVOS", "section-label"))
        self.devices = box(True, 0, "channel-list")
        self.body.append(self.devices)
        heading = box(spacing=8)
        heading.set_margin_top(8)
        heading.append(label("APLICATIVOS", "section-label", True))
        heading.append(label("alto-falante: saída · microfone: entrada", "muted small"))
        self.body.append(heading)
        self.apps = box(True, 0, "channel-list")
        self.body.append(self.apps)
        self.empty = label("Nenhum aplicativo com áudio aberto.", "empty muted")
        self.body.append(self.empty)
        footer = box(spacing=8)
        self.status = label("Conectando…", "small muted", True)
        footer.append(self.status)
        self.connection = label("●", "online")
        footer.append(self.connection)
        footer.append(button("Encerrar", app.quit, "flat"))
        root.append(footer)
        GLib.timeout_add(33, self.tick)

    def open_settings(self):
        if self.settings_window is None:
            self.settings_window = Gtk.Window(title="Opções do Sonora", transient_for=self, destroy_with_parent=True)
            self.settings_window.set_default_size(480, 430)
            self.settings_window.connect("close-request", lambda w: (w.set_visible(False), True)[1])
            content = box(True, 12)
            for side in ("top", "bottom", "start", "end"):
                getattr(content, "set_margin_" + side)(16)
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.set_child(content)
            self.settings_window.set_child(scroll)
            startup = Gtk.CheckButton(label="Iniciar com o computador")
            startup.set_active(AUTOSTART.exists())
            startup.connect("toggled", self.autostart)
            content.append(startup)
            self.boost_check = Gtk.CheckButton(label="Permitir volume até 150%")
            self.boost_check.set_active(self.state.get("boost", False))
            self.boost_check.connect("toggled", self.boost)
            content.append(self.boost_check)
            text = label("Fechar a janela mantém as regras em segundo plano.\nUse Encerrar para sair. A estrela salva as preferências de um app.", "small muted")
            text.set_wrap(True)
            content.append(text)
            scenes = box(True, 8)
            row = box(spacing=8)
            self.scene_name = Gtk.Entry(placeholder_text="Nome da cena")
            self.scene_name.set_hexpand(True)
            row.append(self.scene_name)
            row.append(button("Salvar atual", self.save_scene))
            scenes.append(row)
            self.scenes = box(True, 6)
            scenes.append(self.scenes)
            self.rules = box(True, 6)
            self.hardware = box(True, 6)
            for name, child in (("Cenas", scenes), ("Preferências salvas", self.rules), ("Perfis de hardware", self.hardware)):
                expander = Gtk.Expander(label=name)
                expander.set_child(child)
                content.append(expander)
            self.settings_content = content
            self.update_saved()
        self.settings_window.present()

    def autostart(self, check):
        try:
            if check.get_active():
                AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
                AUTOSTART.write_text(f"[Desktop Entry]\nType=Application\nName=Sonora\nExec={Path.home()}/.local/bin/sonora --background\nTerminal=false\n")
            else:
                AUTOSTART.unlink(missing_ok=True)
        except OSError as exc:
            self.show_error(str(exc))

    def boost(self, check):
        if self.state.get("boost") != check.get_active():
            self.engine.command("boost", check.get_active())

    def save_scene(self):
        name = self.scene_name.get_text().strip()
        if name in self.state.get("scenes", []):
            self.show_error("Já existe uma cena com esse nome.")
            return
        self.engine.command("save_scene", name)
        self.scene_name.set_text("")

    def show_error(self, message):
        self.error_label.set_text(message)
        self.error.set_visible(True)
        return False

    def hide_window(self, *_):
        if self.settings_window:
            self.settings_window.set_visible(False)
        self.set_visible(False)
        self.engine.command("visible", False)
        return True

    def update(self, state):
        self.state = state
        connected = state.get("connected", False)
        self.connection.set_text("●" if connected else "Reconectando…")
        self.body.set_sensitive(connected)
        if not connected:
            return False
        self.max_volume = 150 if state["boost"] else 100
        if self.settings_window:
            self.boost_check.set_active(state["boost"])
        items = [item for kind in ("sink", "source", "playback", "recording") for item in state[kind]]
        keys = {item["key"] for item in items}
        structure_changed = keys != set(self.channels)
        for key in list(self.channels):
            if key not in keys:
                channel = self.channels.pop(key)
                if channel.volume_timer:
                    GLib.source_remove(channel.volume_timer)
                channel.get_parent().remove(channel)
        for item in items:
            if item["key"] not in self.channels:
                channel = Channel(self, item)
                self.channels[item["key"]] = channel
                (self.devices if channel.device else self.apps).append(channel)
            else:
                self.channels[item["key"]].update(item, state)
        previous = {True: None, False: None}
        for item in items:
            channel = self.channels[item["key"]]
            container = self.devices if channel.device else self.apps
            container.reorder_child_after(channel, previous[channel.device])
            previous[channel.device] = channel
        self.empty.set_visible(not state["playback"] and not state["recording"])
        count = len(state["playback"]) + len(state["recording"])
        self.status.set_text(f'{len(state["sink"])} saídas · {len(state["source"])} entradas · {count} canais de apps')
        signature = json.dumps([state["rules"], state["scenes"], state["cards"], [(d["name"], d["title"]) for d in state["sink"] + state["source"]]], sort_keys=True)
        if signature != self.signature:
            self.signature = signature
            self.update_saved()
        if structure_changed:
            GLib.idle_add(self.fit_to_content)
        return False

    def fit_to_content(self):
        natural_height = self.body.measure(Gtk.Orientation.VERTICAL, max(1, self.body.get_width()))[1]
        self.scroll.set_min_content_height(min(natural_height, self.scroll.get_max_content_height()))
        self.set_default_size(780, -1)
        return False

    def update_saved(self):
        if self.settings_window is None or not self.state.get("connected"):
            return
        state = self.state
        clear(self.rules)
        names = {d["name"]: short_device(d["title"]) for d in state["sink"] + state["source"]}
        if not state["rules"]:
            self.rules.append(label("Nenhuma preferência salva.", "small muted"))
        for key, rule in state["rules"].items():
            row = box(spacing=8)
            text = box(True, 2)
            text.set_hexpand(True)
            text.append(label(rule["title"], "channel-name"))
            target = rule.get("target")
            detail = names.get(target, "Dispositivo desconectado") if target else "Padrão do sistema"
            text.append(label(f'{detail} · {rule["volume"]}%' + (" · mudo" if rule["mute"] else ""), "small muted"))
            row.append(text)
            row.append(button("Esquecer", lambda key=key: self.engine.command("forget", key)))
            self.rules.append(row)
        clear(self.scenes)
        for name in state["scenes"]:
            row = box(spacing=8)
            row.append(label(name, expand=True))
            row.append(button("Aplicar", lambda name=name: self.engine.command("load_scene", name)))
            row.append(icon_button("edit-delete-symbolic", "Excluir " + name, lambda name=name: self.engine.command("delete_scene", name)))
            self.scenes.append(row)
        clear(self.hardware)
        for card in state["cards"]:
            row = box(True, 4)
            row.append(label(card["title"], "small muted"))
            choice = Choice(lambda value, index=card["index"]: self.engine.command("profile", index, value), "Perfil de hardware")
            choice.update([(p["name"], p["title"]) for p in card["profiles"]], card["active"])
            row.append(choice)
            self.hardware.append(row)

    def tick(self):
        if self.is_visible():
            for channel in self.channels.values():
                if channel.get_mapped():
                    channel.meter.queue_draw()
        return True


class Sonora(Gtk.Application):
    def __init__(self, background=False):
        super().__init__(application_id="io.github.lol.Sonora", flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None
        self.latest = None
        self.engine = AudioEngine(self.receive_state, self.receive_error)

    def do_startup(self):
        Gtk.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_path(str(ROOT / "style.css"))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)
        self.hold()
        self.engine.visible = False
        self.engine.start()
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self.end)
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self.end)

    def end(self):
        self.quit()
        return False

    def do_command_line(self, command):
        if "--quit" in command.get_arguments():
            self.quit()
        elif "--background" not in command.get_arguments():
            self.activate()
        return 0

    def do_activate(self):
        if self.window is None:
            self.window = Window(self)
        if self.latest:
            self.window.update(self.latest)
        self.engine.command("visible", True)
        self.window.present()

    def receive_state(self, state):
        self.latest = state
        GLib.idle_add(self.deliver_state, state)

    def deliver_state(self, state):
        if self.window and self.window.is_visible():
            self.window.update(state)
        return False

    def receive_error(self, error):
        logging.error(error)
        GLib.idle_add(self.deliver_error, error)

    def deliver_error(self, error):
        if self.window:
            self.window.show_error(error)
        return False

    def do_shutdown(self):
        self.engine.stop()
        Gtk.Application.do_shutdown(self)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(Sonora().run(sys.argv))
