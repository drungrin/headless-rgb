# headless-lights

Controlador RGB headless para Linux, com sincronização opcional de periféricos RGB conectados a um Mac na mesma rede. O projeto usa protocolos diretos para a fita Beelight e OpenRGB para os dispositivos locais compatíveis.

Ele foi criado para manter iluminação consistente sem depender de uma interface gráfica ou do iCUE.

## Recursos

- Controle da fita USB **Beelight V3/AT32**, incluindo cor estática e frames por LED.
- Controle de RAM Corsair RGB, controladores ASUS Aura e fans Corsair iCUE LINK via OpenRGB.
- Efeitos animados sincronizados entre Linux e macOS pelo relógio Unix:
  - Watercolor Spectrum;
  - Stranger Things;
  - Borderlands 4.
- Serviços de usuário do systemd para manter iluminação estática ou efeitos persistentes.
- Agente macOS em C++ que controla periféricos por HID, sem SDK do iCUE.
- Reconexão automática do Scimitar wireless após desconexão ou timeout.
- Plugin de SignalRGB que transmite o canvas do Windows para os periféricos do Mac, por LED.

O projeto controla somente iluminação: não altera velocidade de fans, curvas térmicas nem outros parâmetros de refrigeração.

## Hardware testado

| Plataforma | Dispositivo | Suporte |
| --- | --- | --- |
| Linux | Beelight V3/AT32 (`2e3c:5740`) | 33 pixels, protocolo serial direto |
| Linux | Corsair Vengeance RGB DDR5 | 12 LEDs por módulo, via OpenRGB |
| Linux | ASUS PRIME Z690-P Aura (`0b05:19af`) | Asiahorse Lightsaber-X, 24 LEDs em `Aura Addressable 2` |
| Linux | Corsair iCUE LINK System Hub (`1b1c:0c3f`) | seis fans LX120/LX120-R/LX140-R, 18 LEDs por fan |
| macOS | Corsair K70 MAX | 116 LEDs, HID direto |
| macOS | Corsair MM700 RGB | 3 LEDs, HID direto |
| macOS | Corsair Scimitar Elite Wireless SE | 3 zonas RGB e 12 botões laterais |
| macOS | Logitech G560 | 4 zonas, HID direto |

O suporte é validado para esta combinação de dispositivos. Outros modelos podem funcionar, mas não são garantidos.

## Requisitos

### Linux

- Python 3.11 ou superior;
- OpenRGB;
- `i2c-tools` para a RAM DDR5;
- acesso aos grupos `dialout`, `i2c` e `plugdev`.

Em distribuições baseadas em Debian ou Ubuntu:

```bash
sudo apt install openrgb i2c-tools
sudo usermod -aG dialout,i2c,plugdev "$USER"
```

Saia da sessão e entre novamente (ou reinicie) para que os novos grupos sejam aplicados.

### macOS (opcional)

O agente do Mac requer o `hidapi` do Homebrew e permissões de Acessibilidade para o binário final. As instruções completas estão em [`mac-agent/README.md`](mac-agent/README.md).

## Instalação

Na raiz do repositório, instale o pacote em um ambiente virtual:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

A regra udev incluída limita o acesso direto aos controladores ASUS Aura e Corsair iCUE LINK identificados pelo projeto. Instale-a a partir da raiz do repositório:

```bash
sudo install -o root -g root -m 0644 \
  udev/99-headless-lights-asus-aura.rules \
  /etc/udev/rules.d/99-headless-lights-asus-aura.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=hidraw --action=add
```

## Uso rápido

### Fita Beelight

Descubra a porta serial e consulte o dispositivo:

```bash
headless-lights detect
headless-lights info
```

Aplique cor, brilho ou um frame com uma cor por LED:

```bash
headless-lights color ff6600
headless-lights brightness 60
headless-lights pixels COR_01 COR_02 ... COR_33
```

Use `hold` para manter a porta aberta, responder aos heartbeats do dispositivo e restaurar a iluminação caso o USB seja reconectado:

```bash
headless-lights hold 0000ff --fps 20
```

Para iniciar uma cor estática no login:

```bash
headless-lights service-install 0000ff --fps 20
```

### Dispositivos OpenRGB locais

```bash
# RAM Corsair
headless-lights ram-color 0000ff
headless-lights ram-service-install 0000ff

# Fita Asiahorse ligada ao ASUS Aura
headless-lights aura-color 0000ff
headless-lights aura-service-install 0000ff

# Fans Corsair iCUE LINK
headless-lights hub-hold 0000ff
headless-lights hub-service-install 0000ff
```

O controlador valida a topologia antes de gravar: a configuração testada espera seis zonas de 18 LEDs (108 LEDs no total) no hub e 24 LEDs na zona Aura configurada.

### Efeitos animados

Os efeitos usam o tempo Unix como fase comum, para que as animações Linux e macOS permaneçam alinhadas.

| Efeito | Descrição |
| --- | --- |
| `watercolor` | Faixas suaves de ciano, azul, violeta, magenta, rosa e amarelo claro |
| `stranger-things` | Fundo azul/roxo, chuva e ondas vermelhas, com flashes periódicos |
| `borderlands-4` | Camadas contínuas vermelha, laranja e dourada |

Execute um preview temporário:

```bash
headless-lights effect-preview watercolor --scope hub --seconds 20 --fps 12
headless-lights effect-preview stranger-things --scope pc --seconds 21 --fps 12
headless-lights effect-preview borderlands-4 --scope pc --seconds 20 --fps 12
```

Mantenha um efeito em primeiro plano ou instale-o como serviço:

```bash
headless-lights effect-hold watercolor --scope pc --fps 12
headless-lights effect-service-install borderlands-4 --scope pc --fps 12
```

Os escopos disponíveis são:

| Escopo | Dispositivos |
| --- | --- |
| `hub` | Fans iCUE LINK |
| `local` | Fans, RAM Corsair e Asiahorse |
| `pc` | Fans, RAM Corsair, Asiahorse e Beelight |

Não execute um preview ou uma cor estática no mesmo dispositivo enquanto o serviço de efeitos estiver ativo. Pause o renderer antes de trocar temporariamente a iluminação:

```bash
systemctl --user stop headless-lights-effect.service
# Execute o comando desejado.
systemctl --user start headless-lights-effect.service
```

## Agente macOS

O agente em [`mac-agent/`](mac-agent/) controla K70 MAX, MM700, Scimitar e G560 por HID direto. Ele escuta somente em `127.0.0.1:7531`; o computador Linux o acessa por uma conexão SSH autenticada.

No Mac, instale o agente com:

```bash
cd mac-agent
sh install.sh
```

Depois, no Linux, use um destino SSH configurado:

```bash
headless-lights mac-status --host usuario@mac.local
headless-lights mac-color 0000ff --host usuario@mac.local
headless-lights mac-effect stranger-things --host usuario@mac.local
```

O Scimitar precisa permanecer em modo software para receber animações RGB. Nesse modo, o agente restaura os 12 botões laterais como as teclas `1` a `=` e, portanto, precisa da permissão de Acessibilidade apenas para o executável final do agente.

## SignalRGB no Windows

Além dos comandos acima, o agente aceita **frames por LED** em `127.0.0.1:7532`. É por aí que o SignalRGB, rodando num PC Windows, assume os quatro periféricos do Mac: o K70 aparece no canvas com geometria de teclado, e os demais como zonas.

O agente continua escutando apenas em loopback. O SignalRGB 2.5 expõe UDP, mas não TCP, para add-ons: um bridge local valida os datagramas e encaminha os mesmos bytes para o túnel TCP/SSH. TCP e UDP compartilham o número 7532 sem conflito, e nada novo fica exposto na rede:

```text
SignalRGB --UDP 7532--> bridge local --TCP 7532/SSH--> agente Mac --> HID
```

O supervisor inicia e mantém tanto o bridge quanto o túnel:

```powershell
powershell -ExecutionPolicy Bypass -File windows\start-mac-tunnel.ps1
```

Por padrão ele usa o destino `mac` do seu `~/.ssh/config`; passe `-MacHost` para outro. Para deixá-lo de pé no logon, registre a tarefa agendada documentada no cabeçalho do próprio script.

O add-on é publicado em [`drungrin/signalrgb-mac-bridge`](https://github.com/drungrin/signalrgb-mac-bridge). Instale a URL desse repositório em **Settings → Add-ons**; não copie o arquivo para a pasta de plugins USB. A fonte canônica também permanece em [`signalrgb/`](signalrgb/) para que os testes e o gerador do layout a validem. Se `k70max_layout.h` mudar, regenere e confira:

```bash
python tools/gen_k70_layout.py --write
python tools/gen_k70_layout.py --check
```

Quando o SignalRGB para de enviar frames — PC desligado, aplicativo fechado, túnel caído — cada dispositivo volta sozinho ao efeito local após 3 segundos. Um `mac-color` ou `mac-effect` na porta de comandos tem precedência imediata sobre o streaming, e o streaming retoma no frame seguinte.

Para exercitar essa porta sem o SignalRGB, útil para depurar o agente:

```bash
headless-lights mac-stream --effect watercolor
headless-lights mac-stream --color ff6600 --device k70 --fps 30
```

O formato do frame está documentado em [`mac-agent/README.md`](mac-agent/README.md).

Duas notas de plataforma: o pacote Python instala e roda no Windows, mas os comandos de fita Beelight exigem um host POSIX, e os serviços (`*-service-install`) dependem do systemd — ou seja, continuam sendo o caminho Linux.

## Serviços

A instalação de serviços cria unidades de usuário; não é necessário executar o controlador como root. Consulte o estado dos serviços Linux com:

```bash
systemctl --user status \
  headless-lights-ram.service \
  headless-lights-aura.service \
  headless-lights-hub.service \
  headless-lights-effect.service
```

## Desenvolvimento e testes

Execute a suíte Python a partir da raiz do repositório:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Os testes cobrem o protocolo Beelight, seleção de dispositivos OpenRGB, topologias do hub, unidades systemd, comunicação com o agente macOS, renderização dos efeitos, o protocolo de streaming e o layout gerado do K70.

O lado C++ tem sua própria suíte, que roda em qualquer plataforma:

```bash
sh mac-agent/tests/run.sh
```

Ela compila e executa os testes do parser de frames e faz a verificação de tipos de `agent.cpp` com `-Wall -Wextra -Werror`. Fora do macOS ela usa os stubs de declaração em `mac-agent/tests/shims/`, que reproduzem as assinaturas reais de hidapi, ApplicationServices e sockets BSD — pegam erro de tipo e de aridade, mas não substituem o build real do `install.sh` no Mac.

Se o Node estiver instalado, a suíte Python também executa o plugin do SignalRGB contra um canvas falso e decodifica os frames que ele produz com o parser do projeto (`tests/test_plugin_frames.py`), garantindo que plugin e agente não divirjam em silêncio. Sem Node, esses testes são pulados.

## Licença

Este repositório ainda não contém uma licença de distribuição. Antes de reutilizar, modificar ou redistribuir o código, entre em contato com a pessoa mantenedora. Alguns arquivos em [`mac-agent/`](mac-agent/) possuem atribuições e condições de licença específicas, documentadas no README daquele diretório.
