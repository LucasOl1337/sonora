# Sonora

Mixer compacto para Omarchy e PipeWire, com a paleta grafite e laranja do Sussurro. A janela entra no mosaico do Hyprland como qualquer outro app e nunca comanda o compositor: nada de `hyprctl`, float ou redimensionar a própria tile. Quando a tile é mais alta que o conteúdo, a sobra vira o **monitor de atividade** no rodapé do cartão de Aplicativos: um gráfico ao vivo do nível da saída padrão (últimos segundos, −60 a 0 dBFS) que estica para preencher qualquer altura e desaparece quando a janela flutua no tamanho natural. As colunas — nome, mudo, volume, porcentagem, ações e menu — são alinhadas por grupos de tamanho compartilhados entre todas as linhas, incluindo as de dispositivos sem perfil (**Ativar**) e as de aplicativos. Larguras são fluidas — o volume ocupa o espaço que sobra, nomes e destinos encolhem com reticências, e abaixo de 560px os controles extras (Testar, Usar, rota, legendas) passam para o menu ⋯. O cliente pede no mínimo 360px para o compositor não esmagar a linha. Não há barra de status embaixo: Encerrar fica no cabeçalho, e **Saída padrão**/**Microfone padrão** ocupam uma barra própria no topo.

Abra **Sonora** no lançador do Omarchy ou execute `sonora` no terminal.

## Controles

- **Dispositivos** e **Aplicativos** ficam juntos na mesma lista, sem abas. Cada linha tem nome, mudo, volume, medidor de sinal e escolha do destino.
- **Saída padrão** e **Microfone padrão**, no cabeçalho, escolhem os padrões do sistema. **Usar** na linha do dispositivo faz o mesmo. O menu de três pontos oferece conectores e balanço estéreo.
- Cada saída tem **Testar**, que toca um tom curto no dispositivo escolhido. Cada entrada tem **Ouvir**, que liga a escuta ao vivo na saída padrão; clique novamente em **Parar** para desligar.
- Telas HDMI conectadas sem um perfil publicado (a NVIDIA só libera um HDMI de cada vez) aparecem na lista e no seletor, com o nome do monitor. **Ativar** troca o perfil e define essa saída como padrão. Microfones conectados sem uma entrada publicada continuam visíveis da mesma forma.
- Nos apps, o seletor escolhe a saída ou a entrada. A estrela salva dispositivo, volume e mudo para quando o app reabrir. **Padrão do sistema** acompanha as mudanças do padrão.
- A **engrenagem** reúne início com a sessão, amplificação até 150%, regras salvas, cenas e perfis de hardware.
- Uma cena salva os padrões, volumes, mudo e destinos atuais. Ao aplicar, só altera os dispositivos e apps disponíveis. Não abre apps nem conecta hardware.

Os canais atuais cabem na janela sem rolagem. Novos canais aumentam sua altura, até o espaço disponível no monitor. A rolagem só aparece quando a lista excede fisicamente a altura da tela.

Fechar a janela mantém o Sonora em segundo plano para aplicar regras. Os medidores e a escuta de teste param enquanto a janela está oculta. Para sair, use **Encerrar** ou `sonora --quit`.

Os medidores de reprodução mostram picos reais individuais, em escala de −60 a 0 dBFS. Nos apps de captura, a barra mostra o nível da entrada usada pelo app. O Sonora compartilha as capturas já abertas no servidor de áudio para medir os microfones; não ocupa entradas ociosas nem grava áudio. Capturas diretas pelo ALSA aparecem identificadas na linha do dispositivo quando ativas, sem um medidor fabricado.

A escuta auxiliar dos gestos do X9 pelo Sussurro permanece no headset e fica oculta na lista de apps. Quando o Sussurro está configurado para `FIFINE ... (hw:4,0)`, ele usa esse hardware diretamente. Sua seleção é independente do padrão do sistema. Nesta máquina, **Mic padrão segue o fone** foi desabilitado no Sussurro para preservar a escolha manual feita no Sonora.

Aplicativos aparecem quando criam um canal de áudio. O nome e o ícone vêm do aplicativo real: quando o app se identifica de forma genérica ou não se identifica (Electron dizendo "Chromium", daemons sem nome), o Sonora resolve pelo binário do processo e pelo arquivo .desktop correspondente. Vários canais do mesmo app indo ao mesmo destino viram uma linha só — o tooltip indica a quantidade de canais e volume, mudo e destino valem para o conjunto. Preferências de entrada e de saída são independentes. Apps que acessam ALSA diretamente e ignoram o servidor de áudio não podem ser controlados por este mixer. Apps com seleção interna explícita de hardware podem recriar seus canais ao alterar a configuração interna.

As regras usam o identificador do app ou a combinação de executável e nome, e o nome persistente do dispositivo. Se um dispositivo salvo estiver desconectado, o Sonora mantém a rota disponível e reaplica a preferência quando ele retornar. Uma regra de dispositivo tem prioridade sobre mudanças externas de rota enquanto o Sonora está ativo.

## Instalação

No Omarchy desta máquina, GTK 4, PyGObject, Cairo, libpulse e uv já estavam presentes.

```bash
./install.sh
sonora
```

O ambiente virtual usa os pacotes GTK do Python do sistema. Após uma atualização da versão principal/secundária de Python, recrie `.venv` e rode o instalador novamente.

Preferências e cenas ficam em `~/.config/sonora/preferences.json`. O início automático, quando habilitado, fica em `~/.config/autostart/sonora.desktop`. No Omarchy, a instalação adiciona `~/.config/hypr/sonora.lua` para integrar o app ao mosaico normal, preservando um backup do arquivo principal antes de acrescentar o `require`. Não altera configurações do Sussurro, atalhos ou PipeWire.

## Desenvolvimento e verificação

`audio.py` mantém todas as operações de libpulse em uma única thread asyncio. A UI GTK recebe retratos do estado; os medidores recebem apenas níveis numéricos a 30 Hz. Desconexões do serviço acionam reconexão automática. As preferências são escritas por substituição atômica.

A integração usa [pulsectl-asyncio](https://github.com/mhthies/pulsectl-asyncio), incluindo a leitura de picos por canal com `subscribe_peak_sample`.

```bash
.venv/bin/python tests/integration_audio.py
.venv/bin/python tests/test_microphones.py
```

O teste cria dispositivos virtuais silenciosos, verifica medidores independentes, volumes, mudo, troca de saída e entrada, restauração ao reabrir o app, cenas e liberação dos medidores. Remove os dispositivos ao finalizar e confere que os padrões do sistema foram preservados.

Ele também verifica o tom de saída e o monitoramento de microfone, incluindo ligar/desligar a escuta sem alterar os padrões do sistema.

O teste de microfones inicia PipeWire e WirePlumber em uma sessão privada, sem acesso a hardware, para verificar a troca real do padrão entre duas entradas. Também cobre a descoberta de um microfone conectado sem fonte publicada e a preservação da saída ao ativar o microfone de um headset.

Para testar os widgets GTK numa bancada isolada:

```bash
agent-bench start sonora-test
agent-bench exec sonora-test -- env GDK_SCALE=1 GDK_DPI_SCALE=1 PULSE_SERVER=unix:/run/user/1000/pulse/native "$PWD/.venv/bin/python" "$PWD/tests/ui_smoke.py"
agent-bench stop sonora-test
```

O teste verifica que todas as linhas estão visíveis sem rolagem, com altura compacta, e dirige volume, mudo, rota, estrela, amplificação, cenas, opções de hardware e ocultar/reabrir usando canais virtuais silenciosos.

A bancada também mede o layout: `tests/test_layout.py` monta a janela sem PipeWire, afirma a largura mínima do conteúdo, o modo compacto abaixo de 560px e que a altura natural acompanha a lista em vez de um monitor inteiro.

```bash
agent-bench exec sonora-test -- env GDK_SCALE=1 GDK_DPI_SCALE=1 "$PWD/.venv/bin/python" "$PWD/tests/test_layout.py"
```
