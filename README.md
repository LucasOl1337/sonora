# Sonora

Mixer nativo para Omarchy e PipeWire, com a paleta grafite e laranja do Sussurro.

Abra **Sonora** no lançador do Omarchy ou execute `sonora` no terminal.

## Controles

- Os dois painéis superiores selecionam a saída e o microfone padrão, com volume, mudo e medidor de sinal.
- **Mixer** mostra os canais de reprodução dos apps e os apps capturando áudio. Cada canal tem volume, mudo e escolha de dispositivo.
- **Lembrar para este app** salva dispositivo, volume e mudo. Escolha **Seguir padrão do sistema** para acompanhar a saída ou a entrada padrão. A preferência continua funcionando quando o app reabre.
- **Dispositivos** oferece volume por dispositivo, seleção do padrão, conectores, balanço estéreo e perfis de hardware.
- **Preferências** permite iniciar com a sessão, liberar amplificação até 150% e esquecer regras salvas.
- **Cenas** salva uma combinação dos padrões, volumes, mudo e destinos dos apps atuais. Ao aplicar, só altera os dispositivos e apps disponíveis. Não abre apps nem conecta hardware.

Fechar a janela mantém o Sonora em segundo plano para aplicar regras. Os medidores param enquanto a janela está oculta. Para sair, use **Encerrar** ou `sonora --quit`.

Os medidores de reprodução mostram picos reais individuais, em escala de −60 a 0 dBFS. Nos apps de captura, a barra mostra o nível da entrada usada pelo app. O Sonora analisa o nível do microfone enquanto sua janela está aberta; não grava nem armazena áudio.

Aplicativos aparecem quando criam um canal de áudio. Um app pode ter vários canais. Preferências de entrada e de saída são independentes. Apps que acessam ALSA diretamente e ignoram o servidor de áudio não podem ser controlados por este mixer. Apps com seleção interna explícita de hardware podem recriar seus canais ao alterar a configuração interna.

As regras usam o identificador do app ou a combinação de executável e nome, e o nome persistente do dispositivo. Se um dispositivo salvo estiver desconectado, o Sonora mantém a rota disponível e reaplica a preferência quando ele retornar. Uma regra de dispositivo tem prioridade sobre mudanças externas de rota enquanto o Sonora está ativo.

## Instalação

No Omarchy desta máquina, GTK 4, PyGObject, Cairo, libpulse e uv já estavam presentes.

```bash
./install.sh
sonora
```

O ambiente virtual usa os pacotes GTK do Python do sistema. Após uma atualização da versão principal/secundária de Python, recrie `.venv` e rode o instalador novamente.

Preferências e cenas ficam em `~/.config/sonora/preferences.json`. O início automático, quando habilitado, fica em `~/.config/autostart/sonora.desktop`. A instalação não altera configurações do Sussurro, atalhos, PipeWire ou Hyprland.

## Desenvolvimento e verificação

`audio.py` mantém todas as operações de libpulse em uma única thread asyncio. A UI GTK recebe retratos do estado; os medidores recebem apenas níveis numéricos a 30 Hz. Desconexões do serviço acionam reconexão automática. As preferências são escritas por substituição atômica.

A integração usa [pulsectl-asyncio](https://github.com/mhthies/pulsectl-asyncio), incluindo a leitura de picos por canal com `subscribe_peak_sample`.

```bash
.venv/bin/python tests/integration_audio.py
```

O teste cria dispositivos virtuais silenciosos, verifica medidores independentes, volumes, mudo, troca de saída e entrada, restauração ao reabrir o app, cenas e liberação dos medidores. Remove os dispositivos ao finalizar e confere que os padrões do sistema foram preservados.

Para testar os widgets GTK numa bancada isolada:

```bash
agent-bench start sonora-test
agent-bench exec sonora-test -- env GDK_SCALE=1 GDK_DPI_SCALE=1 PULSE_SERVER=unix:/run/user/1000/pulse/native "$PWD/.venv/bin/python" "$PWD/tests/ui_smoke.py"
agent-bench stop sonora-test
```

O teste dirige os widgets de volume, mudo, rota, preferências, amplificação, cenas, abas e ocultar/reabrir usando canais virtuais silenciosos.
