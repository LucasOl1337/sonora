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
        text.set_max_width_chars(30)
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
    def __init__(self, engine, vertical=False):
        super().__init__()
        self.engine, self.key, self.vertical = engine, None, vertical
        self.display = 0.0
        self.muted = False
        self.set_content_width(12 if vertical else 100)
        self.set_content_height(130 if vertical else 7)
        self.set_draw_func(self.draw)
        self.set_tooltip_text("Pico real do áudio, de −60 a 0 dBFS")

    def draw(self, _, ctx, width, height):
        sample, timestamp = self.engine.levels.get(self.key, (0, 0))
        value = max(0, (20 * math.log10(max(sample, 0.001)) + 60) / 60) if time.monotonic() - timestamp < 0.3 and not self.muted else 0
        self.display = max(value, self.display * 0.83)
        steps, gap = (28, 2) if self.vertical else (55, 2)
        length = height if self.vertical else width
        size = max(1, (length - (steps - 1) * gap) / steps)
        for i in range(steps):
            if i / steps <= self.display and self.display > 0.005:
                color = (0.95, 0.33, 0.08) if i < steps * 0.8 else ((0.96, 0.67, 0.24) if i < steps * 0.95 else (0.96, 0.25, 0.27))
            else:
                color = (0.18, 0.19, 0.22)
            ctx.set_source_rgb(*color)
            if self.vertical:
                ctx.rectangle(0, height - (i + 1) * (size + gap) + gap, width, size)
            else:
                ctx.rectangle(i * (size + gap), 0, size, height)
            ctx.fill()


class Channel(Gtk.Box):
    def __init__(self, window, item, strip=False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add_css_class("card")
        self.window, self.item, self.strip = window, item, strip
        self.updating = False
        self.edit_until = 0
        self.pending = None
        self.volume_timer = None
        self.device = item["kind"] in ("sink", "source")
        self.set_size_request(225 if strip else -1, -1)
        top = box()
        self.icon = Gtk.Image.new_from_icon_name(item["icon"])
        self.icon.set_pixel_size(24)
        self.icon.add_css_class("app-icon")
        top.append(self.icon)
        titles = box(True, 3)
        titles.set_hexpand(True)
        self.title = label(item["title"], "title")
        self.title.set_max_width_chars(26 if strip else 65)
        self.subtitle = label("", "muted small")
        self.subtitle.set_max_width_chars(25 if strip else 60)
        titles.append(self.title)
        titles.append(self.subtitle)
        top.append(titles)
        self.append(top)
        self.readout = label("", "percentage")
        self.mute = Gtk.ToggleButton(label="Silenciar")
        self.mute.connect("toggled", self.on_mute)
        self.mute.set_tooltip_text("Silenciar este canal")
        self.meter = Meter(window.engine, vertical=strip)
        self.meter.key = item["key"]
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.VERTICAL if strip else Gtk.Orientation.HORIZONTAL, 0, window.max_volume, 1)
        self.scale.set_draw_value(False)
        self.scale.set_hexpand(True)
        self.scale.set_tooltip_text("Volume de " + item["title"])
        if strip:
            self.scale.set_inverted(True)
            self.scale.set_size_request(40, 130)
            self.scale.set_hexpand(False)
            self.scale.add_mark(100, Gtk.PositionType.RIGHT, "100")
            self.scale.add_mark(50, Gtk.PositionType.RIGHT, "50")
            self.scale.add_mark(0, Gtk.PositionType.RIGHT, "0")
        self.scale.connect("value-changed", self.on_volume)
        if strip:
            controls = box(spacing=22)
            controls.set_halign(Gtk.Align.CENTER)
            controls.append(self.meter)
            controls.append(self.scale)
            self.readout.set_halign(Gtk.Align.CENTER)
            self.append(self.readout)
            self.append(controls)
            self.append(self.mute)
        else:
            controls = box()
            controls.append(self.scale)
            controls.append(self.readout)
            controls.append(self.mute)
            self.append(controls)
            self.append(self.meter)
        self.route = None
        self.remember = None
        self.default_btn = None
        if not self.device:
            self.append(label("SAÍDA" if item["kind"] == "playback" else "MICROFONE", "eyebrow"))
            self.route = Choice(lambda name: self.send("route", name), "Dispositivo usado por este aplicativo")
            self.append(self.route)
            self.remember = Gtk.CheckButton(label="Lembrar para este app")
            self.remember.add_css_class("small")
            self.remember.connect("toggled", self.on_remember)
            self.append(self.remember)
        else:
            self.default_btn = button("Usar como padrão", lambda: self.send("default"))
            self.append(self.default_btn)
            self.ports = Choice(lambda name: self.send("port", name), "Conector do dispositivo")
            self.append(self.ports)
            if item["channels"] == 2:
                expander = Gtk.Expander(label="Balanço esquerdo / direito")
                balance = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, -1, 1, 0.05)
                balance.set_draw_value(False)
                balance.add_mark(0, Gtk.PositionType.BOTTOM, "Centro")
                balance.connect("value-changed", lambda w: self.send("balance", w.get_value()))
                expander.set_child(balance)
                self.append(expander)
        self.update(item, window.state)

    def send(self, action, *args):
        self.window.engine.command(action, self.item["kind"], self.item["index"], *args)

    def on_volume(self, *_):
        if self.updating:
            return
        value = round(self.scale.get_value())
        self.readout.set_text(f"{value}%")
        self.pending = value
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
        self.title.set_text(item["title"])
        self.title.set_tooltip_text(item["title"])
        self.subtitle.set_text(("PADRÃO · " if item["default"] else "") + ("Em uso" if item["active"] else "Em repouso") if self.device else item["subtitle"])
        self.subtitle.set_tooltip_text(item["name"] if self.device else item["subtitle"])
        self.scale.set_range(0, max(self.window.max_volume, item["volume"]))
        if time.monotonic() > self.edit_until:
            self.scale.set_value(item["volume"])
            self.readout.set_text(f'{item["volume"]}%')
        self.mute.set_active(item["mute"])
        self.mute.set_label("Silenciado" if item["mute"] else "Silenciar")
        self.meter.muted = item["mute"]
        if item["kind"] == "recording":
            self.meter.key = f'source:{item["target"]}'
            self.meter.set_tooltip_text("Nível do microfone usado por este app")
        if self.route:
            devices = state["sink" if item["kind"] == "playback" else "source"]
            rule = state["rules"].get(item["kind"] + ":" + item["identity"])
            target = next((d["name"] for d in devices if d["index"] == item["target"]), None)
            options = [(None, "Seguir padrão do sistema")] + [(d["name"], d["title"]) for d in devices]
            if item["following"] or (rule and rule.get("target") is None):
                target = None
            self.route.update(options, target)
            self.remember.set_active(bool(rule))
        else:
            self.default_btn.set_label("Dispositivo padrão" if item["default"] else "Usar como padrão")
            self.default_btn.set_sensitive(not item["default"])
            self.ports.update([(p["name"], p["title"]) for p in item["ports"]], item["port"])
            self.ports.set_visible(len(item["ports"]) > 1)
        self.updating = False


class Master(Gtk.Box):
    def __init__(self, window, kind):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.add_css_class("master")
        self.set_hexpand(True)
        self.window, self.kind, self.current = window, kind, None
        self.updating = False
        self.edit_until = 0
        self.volume_timer = None
        self.pending = None
        self.append(label("SAÍDA PRINCIPAL" if kind == "sink" else "MICROFONE PRINCIPAL", "eyebrow"))
        self.choice = Choice(self.set_default, "Escolher o dispositivo padrão do sistema")
        self.append(self.choice)
        row = box()
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.scale.set_draw_value(False)
        self.scale.set_hexpand(True)
        self.scale.connect("value-changed", self.volume)
        self.percent = label("0%", "mono small")
        self.mute = Gtk.ToggleButton()
        self.mute.set_icon_name("audio-volume-high-symbolic" if kind == "sink" else "microphone-sensitivity-high-symbolic")
        self.mute.set_tooltip_text("Silenciar saída principal" if kind == "sink" else "Silenciar microfone principal")
        self.mute.connect("toggled", self.toggle)
        row.append(self.mute)
        row.append(self.scale)
        row.append(self.percent)
        self.append(row)
        self.meter = Meter(window.engine)
        self.append(self.meter)

    def set_default(self, name):
        item = next(i for i in self.window.state[self.kind] if i["name"] == name)
        self.window.engine.command("default", self.kind, item["index"])

    def volume(self, *_):
        if not self.updating and self.current:
            value = round(self.scale.get_value())
            self.edit_until = time.monotonic() + 1
            self.percent.set_text(f"{value}%")
            self.pending = (self.current["index"], value)
            if not self.volume_timer:
                self.volume_timer = GLib.timeout_add(60, self.flush_volume)

    def flush_volume(self):
        self.window.engine.command("volume", self.kind, *self.pending)
        self.volume_timer = None
        return False

    def toggle(self, *_):
        if not self.updating and self.current:
            self.window.engine.command("mute", self.kind, self.current["index"], self.mute.get_active())

    def update(self, state):
        self.updating = True
        devices = state[self.kind]
        self.current = next((d for d in devices if d["default"]), None)
        self.choice.update([(d["name"], d["title"]) for d in devices], state["defaults"][self.kind])
        self.scale.set_sensitive(bool(self.current))
        self.mute.set_sensitive(bool(self.current))
        if self.current:
            self.meter.key = self.current["key"]
            self.meter.muted = self.current["mute"]
            self.scale.set_range(0, max(self.window.max_volume, self.current["volume"]))
            if time.monotonic() > self.edit_until:
                self.scale.set_value(self.current["volume"])
                self.percent.set_text(f'{self.current["volume"]}%')
            self.mute.set_active(self.current["mute"])
        else:
            self.meter.key = None
        self.updating = False


class Window(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Sonora")
        self.set_default_size(1080, 940)
        self.set_size_request(740, 580)
        self.app = app
        self.engine = app.engine
        self.state = {}
        self.max_volume = 100
        self.channels = {}
        self.signature = None
        self.cards_signature = None
        self.connect("close-request", self.hide_window)
        headerbar = Gtk.HeaderBar()
        headerbar.set_title_widget(Gtk.Label(label="Sonora"))
        self.set_titlebar(headerbar)
        root = box(True, 18)
        for side in ("top", "bottom", "start", "end"):
            getattr(root, "set_margin_" + side)(24 if side != "top" else 8)
        self.set_child(root)
        header = box(spacing=14)
        icon = Gtk.Image.new_from_file(str(ROOT / "assets" / "sonora.svg"))
        icon.set_pixel_size(46)
        header.append(icon)
        title = box(True, 0)
        title.append(label("SONORA", "brand"))
        title.append(label("Controle de áudio", "muted small"))
        title.set_hexpand(True)
        header.append(title)
        self.connection = label("Conectando…", "online mono")
        header.append(self.connection)
        root.append(header)
        self.error = box()
        self.error.add_css_class("error")
        self.error_label = label("", expand=True)
        self.error_label.set_wrap(True)
        self.error.append(self.error_label)
        self.error.append(button("Fechar", lambda: self.error.set_visible(False), "flat"))
        self.error.set_visible(False)
        root.append(self.error)
        masters = box(spacing=14)
        masters.set_homogeneous(True)
        self.output = Master(self, "sink")
        self.input = Master(self, "source")
        masters.append(self.output)
        masters.append(self.input)
        root.append(masters)
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(120)
        tabs = Gtk.StackSwitcher(stack=self.stack)
        tabs.set_halign(Gtk.Align.START)
        root.append(tabs)
        self.stack.set_vexpand(True)
        root.append(self.stack)
        self.mixer = self.page("mixer", "Mixer")
        heading = box()
        heading.append(label("Aplicativos", "section-title", True))
        self.count = label("", "muted small mono")
        heading.append(self.count)
        self.mixer.append(heading)
        self.empty = label("Nenhum aplicativo reproduzindo áudio.\nAbra uma música, um vídeo ou um jogo para ver seus canais aqui.", "empty")
        self.empty.set_wrap(True)
        self.mixer.append(self.empty)
        self.playback = Gtk.FlowBox()
        self.playback.set_selection_mode(Gtk.SelectionMode.NONE)
        self.playback.set_homogeneous(True)
        self.playback.set_min_children_per_line(2)
        self.playback.set_max_children_per_line(4)
        self.playback.set_column_spacing(14)
        self.playback.set_row_spacing(14)
        self.mixer.append(self.playback)
        self.mixer.append(label("Usando o microfone", "section-title"))
        self.mixer.append(label("A entrada escolhida vale para a captura ativa de cada app.", "muted small"))
        self.recording = box(True, 12)
        self.mixer.append(self.recording)
        self.record_empty = label("Nenhum aplicativo capturando áudio.", "muted")
        self.mixer.append(self.record_empty)
        devices = self.page("devices", "Dispositivos")
        devices.append(label("Saídas de áudio", "section-title"))
        self.sinks = box(True, 12)
        devices.append(self.sinks)
        devices.append(label("Microfones e entradas", "section-title"))
        self.sources = box(True, 12)
        devices.append(self.sources)
        devices.append(label("Perfis de hardware", "section-title"))
        devices.append(label("Escolha os modos disponíveis de cada placa, como analógico, HDMI ou duplex.", "muted small"))
        self.hardware = box(True, 12)
        devices.append(self.hardware)
        prefs = self.page("preferences", "Preferências")
        options = box(True, 15, "card")
        startup = Gtk.CheckButton(label="Iniciar Sonora com o computador")
        startup.set_active(AUTOSTART.exists())
        startup.connect("toggled", self.autostart)
        options.append(startup)
        self.boost_check = Gtk.CheckButton(label="Permitir volume até 150%")
        self.boost_check.set_tooltip_text("Volumes acima de 100% podem distorcer o áudio")
        self.boost_check.connect("toggled", self.boost)
        options.append(self.boost_check)
        info = label("Ao fechar a janela, Sonora continua aplicando as preferências salvas.\nOs medidores param enquanto a janela está oculta. Use Encerrar para sair.", "muted small")
        info.set_wrap(True)
        options.append(info)
        prefs.append(options)
        prefs.append(label("Preferências por aplicativo", "section-title"))
        info = label("Marque “Lembrar para este app” no mixer para salvar dispositivo, volume e mudo.\nA regra será aplicada quando o app voltar a reproduzir ou capturar áudio.", "muted small")
        info.set_wrap(True)
        prefs.append(info)
        self.rules = box(True, 12)
        prefs.append(self.rules)
        scenes = self.page("scenes", "Cenas")
        scenes.append(label("Seu áudio, pronto para cada momento", "section-title"))
        info = label("Salve os dispositivos padrão e os volumes dos canais atuais.\nAo aplicar, os dispositivos desconectados e os apps fechados são ignorados.", "muted")
        info.set_wrap(True)
        scenes.append(info)
        row = box()
        self.scene_name = Gtk.Entry(placeholder_text="Nome da cena, por exemplo: Trabalho")
        self.scene_name.set_hexpand(True)
        row.append(self.scene_name)
        row.append(button("Salvar cena atual", self.save_scene, "accent"))
        scenes.append(row)
        self.scenes = box(True, 12)
        scenes.append(self.scenes)
        footer = box()
        self.status = label("Conectando ao serviço de áudio…", "muted small", True)
        footer.append(self.status)
        footer.append(button("Encerrar", app.quit, "flat"))
        root.append(footer)
        GLib.timeout_add(33, self.tick)

    def page(self, name, title):
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        content = box(True, 16)
        content.set_margin_end(6)
        scroll.set_child(content)
        self.stack.add_titled(scroll, name, title)
        return content

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
        self.max_volume = 150 if check.get_active() else 100
        if self.state.get("boost") != check.get_active():
            self.engine.command("boost", check.get_active())

    def save_scene(self):
        name = self.scene_name.get_text().strip()
        if name in self.state.get("scenes", []):
            self.show_error("Já existe uma cena com esse nome. Escolha outro ou exclua a anterior.")
            return
        self.engine.command("save_scene", name)
        self.scene_name.set_text("")

    def show_error(self, message):
        self.error_label.set_text(message)
        self.error.set_visible(True)
        return False

    def hide_window(self, *_):
        self.set_visible(False)
        self.engine.command("visible", False)
        return True

    def update(self, state):
        self.state = state
        connected = state.get("connected", False)
        self.connection.set_text("● PIPEWIRE / PULSE" if connected else "● RECONECTANDO")
        self.stack.set_sensitive(connected)
        self.output.set_sensitive(connected)
        self.input.set_sensitive(connected)
        if not connected:
            return False
        self.max_volume = 150 if state["boost"] else 100
        self.boost_check.set_active(state["boost"])
        self.output.update(state)
        self.input.update(state)
        self.count.set_text(f'{len(state["playback"]):02d} CANAIS')
        self.empty.set_visible(not state["playback"])
        self.record_empty.set_visible(not state["recording"])
        containers = {"playback": self.playback, "recording": self.recording, "sink": self.sinks, "source": self.sources}
        keys = {item["key"] for kind in containers for item in state[kind]}
        for key in list(self.channels):
            if key not in keys:
                channel = self.channels.pop(key)
                if channel.volume_timer:
                    GLib.source_remove(channel.volume_timer)
                parent = channel.get_parent()
                if isinstance(parent, Gtk.FlowBoxChild):
                    parent.get_parent().remove(parent)
                else:
                    parent.remove(channel)
        for kind, container in containers.items():
            for item in state[kind]:
                if item["key"] not in self.channels:
                    channel = Channel(self, item, strip=kind == "playback")
                    self.channels[item["key"]] = channel
                    container.append(channel)
                else:
                    self.channels[item["key"]].update(item, state)
        signature = json.dumps([state["rules"], state["scenes"], [(i["name"], i["title"]) for i in state["sink"] + state["source"]]], sort_keys=True)
        if signature != self.signature:
            self.signature = signature
            self.update_saved(state)
        signature = json.dumps(state["cards"], sort_keys=True)
        if signature != self.cards_signature:
            self.cards_signature = signature
            clear(self.hardware)
            for card in state["cards"]:
                row = box(True, 10, "card")
                row.append(label(card["title"], "title"))
                choice = Choice(lambda value, index=card["index"]: self.engine.command("profile", index, value), "Perfil de hardware")
                choice.update([(p["name"], p["title"]) for p in card["profiles"]], card["active"])
                row.append(choice)
                self.hardware.append(row)
        self.status.set_text(f'{len(state["sink"])} saídas · {len(state["source"])} entradas · {len(state["rules"])} preferências salvas')
        return False

    def update_saved(self, state):
        clear(self.rules)
        names = {d["name"]: d["title"] for d in state["sink"] + state["source"]}
        if not state["rules"]:
            self.rules.append(label("Nenhuma preferência salva ainda.", "empty"))
        for key, rule in state["rules"].items():
            row = box(css="card")
            text = box(True, 5)
            text.set_hexpand(True)
            text.append(label(rule["title"] + (" · reprodução" if rule["kind"] == "playback" else " · microfone"), "title"))
            target = rule.get("target")
            text.append(label(names.get(target, "Dispositivo desconectado") if target else "Segue o padrão do sistema", "muted small"))
            text.append(label(f'{rule["volume"]}% · ' + ("silenciado" if rule["mute"] else "som ligado"), "small mono"))
            row.append(text)
            row.append(button("Esquecer", lambda key=key: self.engine.command("forget", key), "flat"))
            self.rules.append(row)
        clear(self.scenes)
        if not state["scenes"]:
            self.scenes.append(label("Crie sua primeira cena com o áudio do jeito que você gosta.", "empty"))
        for name in state["scenes"]:
            row = box(css="card")
            row.append(label(name, "title", True))
            row.append(button("Aplicar", lambda name=name: self.engine.command("load_scene", name), "accent"))
            row.append(button("Excluir", lambda name=name: self.engine.command("delete_scene", name), "flat"))
            self.scenes.append(row)

    def tick(self):
        if self.is_visible():
            self.output.meter.queue_draw()
            self.input.meter.queue_draw()
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
