# mac-agent

Ferramentas do agente que aplica no Mac cores fixas e efeitos definidos pelo PC.

`icue_probe.cpp` usa o SDK oficial da Corsair para validar conexão com o iCUE e
enumerar dispositivos/LEDs. Sem argumentos ele é somente leitura. Com
`--color RRGGBB`, aplica uma camada compartilhada por oito segundos; não altera
perfis, teclas ou macros.

O SDK não é redistribuído neste repositório. Os headers e a biblioteca devem vir
do DMG oficial `iCUESDK_4.0.84.dmg`.

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

`agent.cpp` reúne os três backends validados e escuta somente em
`127.0.0.1:7531`. O PC envia `COLOR RRGGBB`, `EFFECT WATERCOLOR` ou `STATUS`
através de uma sessão SSH. Por padrão o binário controla K70 MAX, Scimitar e
G560; a instalação usa `--include-mm700` para incluir explicitamente o mousepad.

`--effect watercolor` executa localmente o mesmo renderer temporal usado no PC,
com gradiente por tecla no K70 e amostras independentes por zona no MM700, G560
e Scimitar. O tempo Unix mantém a fase alinhada entre as duas máquinas.

Para compilar, instalar e carregar o LaunchAgent no Mac:

```sh
sh install.sh /caminho/para/iCUESDK.framework
```

O instalador usa o `hidapi` do Homebrew, grava os arquivos em
`~/Library/Application Support/headless-lights` e instala
`~/Library/LaunchAgents/com.headless-lights.agent.plist`. O processo inicia em
Watercolor Spectrum, inclui explicitamente o MM700, reinicia se o iCUE ainda não
estiver disponível e não expõe uma porta na rede.
