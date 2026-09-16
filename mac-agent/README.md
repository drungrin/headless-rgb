# mac-agent

Ferramentas do agente que aplica no Mac cores fixas e efeitos definidos pelo PC.

`icue_probe.cpp` permanece apenas como diagnóstico histórico do SDK. O agente de
produção não carrega o iCUE nem redistribui seu framework.

`k70max_probe.cpp`, `k70max_layout.h` e `mm700_probe.cpp` validam os backends HID
diretos. O protocolo e o mapa físico derivam respectivamente das implementações
K70 MAX e MM700 do OpenLinkHub; esses arquivos e `agent.cpp` são distribuídos
sob GPL-3.0-or-later.

`g560_probe.cpp` enumera a interface Lightsync do Logitech G560. Com
`--color RRGGBB`, aplica a mesma cor às quatro zonas usando o protocolo HID
documentado no driver do
[OpenRGB](https://gitlab.com/CalcProgrammer1/OpenRGB/-/tree/master/Controllers/LogitechController).
Esse arquivo é disponibilizado sob GPL-2.0-or-later, acompanhando a licença da
implementação de referência.

`scimitar_probe.cpp` testa o fallback HID não exclusivo para o Scimitar Elite
Wireless SE através do receptor Slipstream (`1b1c:2b00`, endpoint `09`). Sem
argumentos ele apenas enumera; com
`--color RRGGBB`, usa somente modo de software, endpoint RGB e escrita de cor.
O formato vem do OpenLinkHub e o arquivo é GPL-3.0-or-later.

`scimitar_input_probe.cpp` documenta o bitmask dos 12 botões laterais recebido
pela interface 2 do Slipstream. O agente converte esses bits em `1` até `=` via
CoreGraphics, sem observar as teclas do K70.

`agent.cpp` reúne quatro backends HID diretos e escuta somente em
`127.0.0.1:7531`. O PC envia `COLOR RRGGBB`, `EFFECT WATERCOLOR`,
`EFFECT STRANGER-THINGS`, `EFFECT BORDERLANDS-4` ou `STATUS` através de uma
sessão SSH. Por padrão o
binário controla K70 MAX, MM700, Scimitar e G560.

## Porta de streaming (127.0.0.1:7532)

Além da porta de comandos, o agente aceita frames por LED em `127.0.0.1:7532`.
É por onde o SignalRGB, rodando no PC, pinta os quatro periféricos. A porta
também é loopback: o PC chega nela por um túnel SSH
(`ssh -N -L 7532:127.0.0.1:7532`), então nada novo fica exposto na rede.

O protocolo está em [`stream_protocol.h`](stream_protocol.h). Cada frame é um
cabeçalho de 6 bytes seguido de triplas RGB, em little-endian:

| offset | bytes | campo | valor |
| --- | --- | --- | --- |
| 0 | 1 | magic0 | `0x53` (`'S'`) |
| 1 | 1 | magic1 | `0x47` (`'G'`) |
| 2 | 1 | versão | `0x01` |
| 3 | 1 | dispositivo | 0 = K70, 1 = MM700, 2 = G560, 3 = Scimitar |
| 4 | 2 | tamanho | uint16 LE, sempre `3 × LEDs` do dispositivo |
| 6 | tamanho | payload | R, G, B por LED |

| dispositivo | id | LEDs | payload | frame |
| --- | --- | --- | --- | --- |
| K70 MAX | 0 | 142 | 426 | 432 |
| MM700 | 1 | 3 | 9 | 15 |
| G560 | 2 | 4 | 12 | 18 |
| Scimitar | 3 | 3 | 9 | 15 |

O K70 recebe os **142 canais de hardware**, não os 116 que acendem: o índice do
frame é o índice em `kK70LedCoordinates`, e os canais sem LED físico vão pretos.
Assim o agente não precisa de tabela de mapeamento própria — quem conhece o mapa
é o cliente, que já precisa dele para posicionar as teclas.

O `tamanho` é validado contra a constante exata do dispositivo, o que torna o
cabeçalho um discriminador de cinco campos e permite ressincronizar no meio de um
fluxo corrompido. Não há checksum (o TCP já tem) e **o agente nunca escreve nessa
porta**: é um fluxo de mão única, sem ACK e sem handshake.

Comportamento do agente:

- **Coalescing** — só o frame mais recente de cada dispositivo é aplicado. Um
  dispositivo que não acompanha descarta frames em vez de acumular atraso.
- **Pacing** — intervalo mínimo por dispositivo (K70 33ms, MM700 e Scimitar 16ms,
  G560 40ms). O K70 custa quatro round trips HID bloqueantes por frame.
- **Fallback** — após 3s sem frames, o dispositivo volta ao efeito local
  configurado no LaunchAgent.
- **Precedência** — um `COLOR` ou `EFFECT` na porta 7531 retoma os quatro
  dispositivos na hora, para que cada `k70=ok` da resposta corresponda a uma
  escrita real. O streaming retoma no frame seguinte.

O payload é binário: quem escrever um cliente deve enviar **bytes**, nunca uma
string de texto. Medido neste projeto: uma rampa de 256 bytes enviada como
string só sobrevive se o runtime a codificar em latin1; em UTF-8 todo byte
`>= 0x80` vira dois (`0x80` → `c2 80`), o que transformaria um frame de 432
bytes do K70 em até 640 bytes de lixo. O plugin monta um array de inteiros
justamente por isso.

Para exercitar essa porta sem o SignalRGB, do PC:

```sh
headless-lights mac-stream --effect watercolor
headless-lights mac-stream --color ff6600 --device k70
```

`--effect watercolor` executa localmente o mesmo renderer temporal usado no PC,
com gradiente por tecla no K70 e amostras independentes por zona no MM700, G560
e Scimitar. O tempo Unix mantém a fase alinhada entre as duas máquinas.

`--effect stranger-things` reproduz somente a animação ambiente do perfil, sem
captura de teclas ou camadas reativas.

`--effect borderlands-4` reproduz as camadas contínuas vermelha, laranja e
dourada do perfil, sem os efeitos originais acionados por tecla.

Para compilar, instalar e carregar o LaunchAgent no Mac:

```sh
sh install.sh
```

Se `/usr/bin/clang++` estiver bloqueado pela licença do Xcode (o erro cita
`sudo xcodebuild -license`), o instalador cai automaticamente para o compilador
das Command Line Tools, que não tem essa exigência. Aceitar a licença também
resolve, mas exige um terminal interativo.

O instalador usa o `hidapi` do Homebrew, grava os arquivos em
`~/Library/Application Support/headless-lights` e instala
`~/Library/LaunchAgents/com.headless-lights.agent.plist`. O processo inicia em
Borderlands 4, reinicia se um dispositivo for reconectado e não expõe uma
porta na rede.

O Scimitar precisa permanecer em modo software para aceitar RGB animado. Nesse
modo, o agente lê o bitmask dos botões laterais pela interface vendor Slipstream
e publica `1` até `=` via CoreGraphics. Conceda Acessibilidade somente ao
executável final:

```text
~/Library/Application Support/headless-lights/bin/headless-lights-agent
```

Não conceda Acessibilidade a `sshd-keygen-wrapper`.

Como o instalador usa assinatura ad-hoc, o TCC indexa o binário pelo CDHash.
Recompilar a partir de um fonte alterado muda esse hash e **invalida a
autorização**, mesmo que a entrada continue aparecendo na lista — o sintoma é
`scimitar=error` com `scimitar-buttons=unavailable`. Nesse caso, remova e
adicione novamente `headless-lights-agent` na lista de Acessibilidade, e
reinicie o agente:

```sh
launchctl kickstart -k gui/$(id -u)/com.headless-lights.agent
```

Recompilar o *mesmo* fonte reproduz o mesmo hash e preserva a autorização. Ao
final, o `install.sh` informa se a permissão está ativa (`accessibility: granted`),
para que uma quebra apareça na hora em vez de virar `scimitar=error` mais tarde.

O backend do Scimitar considera uma resposta vazia como timeout e invalida o
endpoint RGB. Quando o mouse volta ao wireless, o agente tenta novamente após
dois segundos, restaura a animação e mantém o mapeamento lateral ativo.
