# headless-lights

Controlador RGB headless que sincroniza dispositivos ligados a este PC Linux e
a um Mac na mesma rede. Há suporte a cores fixas, quadros por LED e ao efeito
animado Watercolor Spectrum.

## Hardware validado

### PC Linux

- fita Beelight V3/AT32 (`2e3c:5740`), com 33 pixels;
- duas memórias Corsair Vengeance RGB DDR5, com 12 LEDs cada;
- ASUS PRIME Z690-P AURA (`0b05:19af`);
- Asiahorse Lightsaber-X mATX, com 24 LEDs no `Aura Addressable 2`;
- Corsair iCUE LINK System Hub (`1b1c:0c3f`);
- seis fans LX120/LX120-R/LX140-R, com 18 LEDs por fan.

### Mac

- Corsair K70 MAX, com 116 LEDs;
- Corsair MM700 RGB, com 3 LEDs;
- Corsair Scimitar Elite Wireless SE, com 3 zonas;
- Logitech G560, com 4 zonas.

O controlador não altera rotação, curvas térmicas ou outros parâmetros de
refrigeração dos fans.

## Instalação no Linux

Instale o OpenRGB e as ferramentas de acesso ao SMBus:

```bash
sudo apt install openrgb i2c-tools
sudo usermod -aG dialout,i2c,plugdev "$USER"
```

Instale a regra restrita aos controladores ASUS AURA e Corsair iCUE LINK
validados neste computador:

```bash
sudo install -o root -g root -m 0644 \
  /home/michel/projects/headless-lights/udev/99-headless-lights-asus-aura.rules \
  /etc/udev/rules.d/99-headless-lights-asus-aura.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=hidraw --action=add
```

Encerre a sessão e entre novamente, ou reinicie o computador, para que os
serviços de usuário herdem os grupos novos. Depois instale o projeto:

```bash
cd /home/michel/projects/headless-lights
python3 -m venv .venv
.venv/bin/pip install -e .
```

## Beelight

O protocolo serial `55 AA 5A` foi recuperado do Beelight V3 e validado no
hardware. Comandos disponíveis:

```bash
headless-lights detect
headless-lights info
headless-lights on
headless-lights off
headless-lights brightness 60
headless-lights color ff6600
headless-lights pixels COR_01 COR_02 ... COR_33
headless-lights hold 0000ff --fps 20
```

`pixels` exige exatamente uma cor por LED. O hardware analisado declara 33
pixels e dois canais (`33, 0`). Um comando estático isolado não permanece no
controlador; `hold` mantém a porta aberta, transmite por `RGB_TRANSFER`, responde
a heartbeats e reconecta se o USB cair.

Para manter somente uma cor fixa na Beelight:

```bash
headless-lights service-install 0000ff --fps 20
```

Esse comando instala `headless-lights.service`. Ele não deve permanecer ativo ao
mesmo tempo que o Watercolor com `--scope pc`, pois ambos usam a mesma porta
serial. Na instalação atual, o serviço estático está desabilitado e o renderer
Watercolor é o proprietário da Beelight.

## Dispositivos OpenRGB locais

As Vengeance DDR5 são filtradas por fabricante e `Type: DRAM` antes da escrita:

```bash
headless-lights ram-color 0000ff
headless-lights ram-service-install 0000ff
```

A Asiahorse é limitada à ASUS PRIME Z690-P, zona `Aura Addressable 2`, com 24
LEDs e brilho máximo:

```bash
headless-lights aura-color 0000ff
headless-lights aura-service-install 0000ff
```

O iCUE LINK System Hub é detectado pelo nome e tipo, sem depender de seu índice
no OpenRGB. Cada zona LX precisa receber um quadro próprio de 18 LEDs:

```bash
headless-lights hub-hold 0000ff
headless-lights hub-service-install 0000ff
```

O backend falha de modo seguro se a quantidade de zonas ou LEDs estiver
incompleta. Os seis fans atuais totalizam 108 LEDs.

## Agente do Mac

O agente em `mac-agent/` controla o K70 MAX e o **MM700** pelo SDK oficial do
iCUE. O Scimitar e o G560 usam backends HID diretos. Ele roda como LaunchAgent,
escuta apenas em `127.0.0.1:7531` e é acessado por este PC através da conexão SSH
autenticada.

O alvo padrão é `michel@172.16.0.104`:

```bash
headless-lights mac-status
headless-lights mac-color 0000ff
headless-lights mac-effect watercolor
```

`mac-color` aplica a cor ao K70 MAX, MM700, Scimitar e G560. Outro alvo pode ser
informado com `--host usuario@endereco`. A compilação e instalação do agente
estão descritas em [`mac-agent/README.md`](mac-agent/README.md).

## Watercolor Spectrum

O renderer recria o aspecto validado a partir da referência visual: faixas
largas de ciano, azul, violeta, magenta, rosa e amarelo claro, com interpolação
suave e deriva espacial. Ele roda a 12 FPS e usa o tempo Unix como relógio de
fase comum entre Linux e Mac.

Escopos disponíveis no Linux:

| Escopo | Dispositivos |
| --- | --- |
| `hub` | fans iCUE LINK |
| `local` | fans, Vengeance e Asiahorse |
| `pc` | fans, Vengeance, Asiahorse e Beelight |

O Mac não faz parte do significado de `--scope pc`: seu LaunchAgent executa o
mesmo algoritmo localmente e usa o mesmo relógio. O efeito inclui K70 MAX,
**MM700**, Scimitar e G560.

Previews temporários:

```bash
headless-lights effect-preview watercolor --scope hub --seconds 20 --fps 12
headless-lights effect-preview watercolor --scope local --seconds 20 --fps 12
headless-lights effect-preview watercolor --scope pc --seconds 20 --fps 12
```

Antes de executar um preview ou uma cor estática local enquanto o Watercolor
persistente estiver ativo, pause o renderer; caso contrário, dois produtores
enviarão quadros ao mesmo tempo:

```bash
systemctl --user stop headless-lights-watercolor.service
# execute o preview ou comando estático
systemctl --user start headless-lights-watercolor.service
```

No Mac, `mac-color` troca o agente para cor estática. Use `mac-effect watercolor`
para voltar à animação.

Execução contínua em primeiro plano ou como serviço:

```bash
headless-lights effect-hold watercolor --scope pc --fps 12
headless-lights effect-service-install watercolor --scope pc --fps 12
```

O instalador grava e inicia `headless-lights-watercolor.service`.

## Serviços e ordem de inicialização

| Serviço | Tipo | Responsabilidade |
| --- | --- | --- |
| `headless-lights-ram.service` | oneshot | aplica o estado inicial das Vengeance |
| `headless-lights-aura.service` | oneshot | configura a Asiahorse depois da RAM |
| `headless-lights-hub.service` | contínuo | mantém o hub e o servidor OpenRGB em `127.0.0.1:6742` |
| `headless-lights-watercolor.service` | contínuo | envia os quadros do efeito após o hub iniciar |
| `com.headless-lights.agent` | LaunchAgent no Mac | anima K70, MM700, Scimitar e G560 |

Estado dos serviços Linux:

```bash
systemctl --user status \
  headless-lights-ram.service \
  headless-lights-aura.service \
  headless-lights-hub.service \
  headless-lights-watercolor.service
```

## Adição dos dois fans futuros

Depois de conectar os dois novos fans, reinicie o servidor para redetectar a
topologia e depois o renderer para obter as oito zonas novas:

```bash
systemctl --user restart headless-lights-hub.service
systemctl --user restart headless-lights-watercolor.service
```

O backend aceitará automaticamente oito fans quando o hub reportar 144 LEDs —
oito zonas de 18 LEDs. Uma topologia parcial é rejeitada sem enviar quadros.

## Testes

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Os testes cobrem o protocolo Beelight, transporte SSH, seleção segura dos
dispositivos OpenRGB, topologias de seis e oito fans, unidades systemd e o
renderer Watercolor.

## Próximas expansões

- novos efeitos sobre a mesma camada de renderização;
- calibração opcional de brilho por dispositivo;
- layouts espaciais configuráveis para representar a posição física de cada
  componente no cockpit.
