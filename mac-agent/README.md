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

Como o instalador usa assinatura ad-hoc, recompilar o agente altera sua identidade
para a TCC. Depois de uma atualização do binário, pode ser necessário remover e
adicionar novamente somente `headless-lights-agent` na lista de Acessibilidade.

O backend do Scimitar considera uma resposta vazia como timeout e invalida o
endpoint RGB. Quando o mouse volta ao wireless, o agente tenta novamente após
dois segundos, restaura a animação e mantém o mapeamento lateral ativo.
