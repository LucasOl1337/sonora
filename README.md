# Sonora

Mixer compacto para Omarchy e PipeWire, com a paleta grafite e laranja do Sussurro. A janela abre flutuante e ajusta a altura à lista de canais.

Abra **Sonora** no lançador do Omarchy ou execute `sonora` no terminal.

## Controles

- **Dispositivos** e **Aplicativos** ficam juntos na mesma lista, sem abas. Cada linha tem nome, mudo, volume, medidor de sinal e escolha do destino.
- Nos dispositivos, **Usar** define a saída ou o microfone padrão. O menu de três pontos oferece conectores e balanço estéreo.
- Nos apps, o seletor escolhe a saída ou a entrada. A estrela salva dispositivo, volume e mudo para quando o app reabrir. **Padrão do sistema** acompanha as mudanças do padrão.
- A **engrenagem** reúne início com a sessão, amplificação até 150%, regras salvas, cenas e perfis de hardware.
- Uma cena salva os padrões, volumes, mudo e destinos atuais. Ao aplicar, só altera os dispositivos e apps disponíveis. Não abre apps nem conecta hardware.

Os canais atuais cabem na janela sem rolagem. Novos canais aumentam sua altura, até o espaço disponível no monitor. A rolagem só aparece quando a lista excede fisicamente a altura da tela.

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

Preferências e cenas ficam em `~/.config/sonora/preferences.json`. O início automático, quando habilitado, fica em `~/.config/autostart/sonora.desktop`. No Omarchy, a instalação adiciona `~/.config/hypr/sonora.lua` para abrir o app flutuante, preservando um backup do arquivo principal antes de acrescentar o `require`. Não altera configurações do Sussurro, atalhos ou PipeWire.

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

O teste verifica que todas as linhas estão visíveis sem rolagem, com altura compacta, e dirige volume, mudo, rota, estrela, amplificação, cenas, opções de hardware e ocultar/reabrir usando canais virtuais silenciosos.
