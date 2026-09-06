#!/usr/bin/env bash
set -euo pipefail
sonora_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python -c "import gi; gi.require_version('Gtk','4.0'); gi.require_foreign('cairo')"
python -m venv --system-site-packages "$sonora_root/.venv"
uv pip install --python "$sonora_root/.venv/bin/python" -r "$sonora_root/requirements.txt"
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/scalable/apps"
python - "$sonora_root" <<'PY'
from pathlib import Path
import shlex
import shutil
import sys
import time
root = Path(sys.argv[1])
home = Path.home()
launcher = home / '.local/bin/sonora'
launcher.write_text('#!/usr/bin/env bash\nexec ' + shlex.quote(str(root / '.venv/bin/python')) + ' ' + shlex.quote(str(root / 'app.py')) + ' "$@"\n')
launcher.chmod(0o755)
desktop = home / '.local/share/applications/sonora.desktop'
desktop.write_text(f'''[Desktop Entry]
Type=Application
Name=Sonora
GenericName=Mixer de volume
Comment=Volume, microfones e dispositivos por aplicativo
Exec="{launcher}"
Icon=sonora
Terminal=false
Categories=AudioVideo;Audio;Mixer;
Keywords=volume;mixer;audio;som;microfone;pipewire;
StartupNotify=true
StartupWMClass=io.github.lol.Sonora
Actions=Quit;

[Desktop Action Quit]
Name=Encerrar Sonora
Exec="{launcher}" --quit
''')
shutil.copy2(root / 'assets/sonora.svg', home / '.local/share/icons/hicolor/scalable/apps/sonora.svg')
hypr = home / '.config/hypr'
main = hypr / 'hyprland.lua'
if main.exists():
    rule = hypr / 'sonora.lua'
    source = root / 'contrib/omarchy/sonora.lua'
    if rule.exists() and rule.read_bytes() != source.read_bytes():
        shutil.copy2(rule, rule.with_name(f'sonora.lua.bak.{time.time_ns()}'))
    shutil.copy2(source, rule)
    if 'require("hypr.sonora")' not in main.read_text():
        shutil.copy2(main, main.with_name(f'hyprland.lua.bak.{time.time_ns()}'))
        with main.open('a') as config:
            config.write('\nrequire("hypr.sonora")\n')
print('Sonora instalado. Abra pelo lançador ou execute sonora.')
PY
if command -v update-desktop-database >/dev/null; then
    update-desktop-database "$HOME/.local/share/applications"
fi
if command -v hyprctl >/dev/null && test -n "${HYPRLAND_INSTANCE_SIGNATURE:-}"; then
    hyprctl reload
    sonora_config_errors="$(hyprctl configerrors)"
    if test -n "$sonora_config_errors"; then
        echo "$sonora_config_errors" >&2
        exit 1
    fi
fi
