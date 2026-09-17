# Botões laterais do Scimitar Elite Wireless SE no Windows

## Contexto

Com o mouse no switch USB-C apontado para o Windows, os 12 botões laterais não
produzem nada — o mesmo sintoma que o macOS tinha antes do agente, e sem iCUE
como requisito.

A causa foi confirmada no hardware nesta sessão, e é idêntica à do macOS:

1. O SignalRGB coloca o Scimitar em **modo software** para poder pintar o RGB.
   Nesse modo o mouse para de emitir os botões laterais como entrada nativa e
   passa a reportá-los como um **bitmask vendor** na interface 2 do dongle
   Slipstream (`vid_1b1c&pid_2b00&mi_02`, usage page `0xFF42`, usage `0x0002`).
   Os bits 5–16 são os 12 botões — o mesmo `0x0001ffe0` que
   `mac-agent/agent.cpp` já usa.
2. O plugin nativo `Corsair_Bragi_Device.js` **já decodifica** esse bitmask e
   mapeia os bits 5–16 para `"Keypad 1"`..`"Keypad 12"`, mas então entrega os
   eventos ao motor de macros do SignalRGB via `mouse.sendEvent(..., "Button Press")`.
3. A UI que ligaria esses eventos a alguma ação **está quebrada nesta build**.
   O log diz, toda vez que a aba Macros é aberta:

   ```
   ThirdpartyMacroTab.qml:2:1: "../Macroblocks/": no such directory
   ```

   Ou seja: os eventos saem do plugin e não chegam a lugar nenhum. É por isso
   que o dispositivo aparece "com macros" e mesmo assim nada funciona.

Dois fatos tornam a correção limpa, ambos verificados:

- `keyboard.sendHid(vkCode, {released})` injeta uma **Virtual-Key do Windows**
  de verdade. Confirmado nos metadados moc de `SignalRgb.exe`
  (`sendHid | QJSValue | aiVKCode | axOptions`, `"sendHid(): vkCode must be a number!"`)
  e pelo próprio plugin, que já usa `0xAD/0xAE/0xAF/0xB0–0xB3` para mídia e
  `0x05/0x06` (XBUTTON1/2) para Forward/Back.
- Um **add-on sobrescreve um plugin nativo** com o mesmo VID:PID. O log mostra
  isso acontecendo hoje com outro dispositivo:

  ```
  PluginCrawler.WARNING - HID plugin with id 0x320F:0x5088 already exists.
  Overwriting with new path: .../cache/addons/.../GMMK_Numpad_QMK.js
  ```

Resultado pretendido: os 12 botões laterais funcionam no Windows sem iCUE, com
o mapeamento configurável dentro do SignalRGB, e o caminho de RGB permanece
byte a byte o do plugin original — mexer na iluminação não encosta nos botões.

Decisões já tomadas com o usuário: escopo **só dentro do SignalRGB** (é ele quem
mantém o modo software, e já roda sempre nesta máquina) e mapeamento padrão
`1 2 3 4 5 6 7 8 9 0 - =`, igual ao que o agente do macOS injeta, para o mouse
se comportar do mesmo jeito dos dois lados do switch.

## Abordagem

Um add-on nosso que substitui o plugin Bragi **apenas para o dongle Slipstream**
(`ProductId()` → `[0x2B00]`). K70 MAX (`0x1BC0`), MM700 e iCUE LINK continuam no
plugin nativo, porque o crawler indexa por par VID:PID.

O fork é derivado do plugin instalado por um script, não copiado à mão, para que
uma atualização do SignalRGB seja um comando e um diff visível.

### Arquivos

| Arquivo | Papel |
| --- | --- |
| `tools/vendor_bragi.py` | lê o `Corsair_Bragi_Device.js` instalado (do `app-*` mais novo), aplica nosso patch por âncoras de texto e escreve o fork. `--check` falha quando uma âncora não bate mais, ou seja, quando o upstream mudou embaixo de nós |
| `signalrgb/corsair-bragi-scimitar.js` | o fork gerado, versionado, fonte canônica (mesmo papel de `signalrgb/beelight.js`) |
| `tools/sync_addon.py` | novo alvo `bragi`, publicando o fork no checkout do add-on com LF, como os outros |
| `tools/scimitar_input_probe.py` | já escrito nesta sessão: contrapartida Windows de `mac-agent/scimitar_input_probe.cpp`, abre a interface vendor em leitura e imprime o bitmask |
| `signalrgb/tests/bragi_harness.mjs` | stubs de `device`, `keyboard`, `mouse`, `battery` e das 12 propriedades |
| `signalrgb/tests/dump_side_buttons.mjs` | roda o plugin sob Node e imprime, em JSON, os `sendHid` que ele emitiu |
| `signalrgb/tests/signalrgb-loader.mjs` | estender para resolver `@SignalRGB/Errors.js` e `@SignalRGB/DeviceDiscovery` |
| `tests/test_bragi_side_buttons.py` | asserções em Python, no molde de `tests/test_plugin_frames.py` (pulado sem Node) |
| `README.md` | subseção nova em "SignalRGB on Windows" |

### O patch sobre o upstream

Quatro edições ancoradas, todas fora do caminho de RGB:

1. **Cabeçalho** — `Name()` distinto, `ProductId()` → `[0x2B00]`, crédito ao
   WhirlwindFX e nota de que o arquivo é gerado por `tools/vendor_bragi.py`.
2. **`ControllableParameters()`** — 12 comboboxes no grupo `side buttons`,
   rótulos `Side Button 1`..`12`, padrões `1`..`=`. Valores oferecidos: dígitos
   `1`–`0`, `-`, `=`, `F13`–`F24`, `Numpad 0`–`9`, `None` (botão não faz nada) e
   `SignalRGB macro` (cai no `mouse.sendEvent` original, caso a aba de macros
   volte a funcionar um dia). O bloco `/* global ... */` do eslint ganha as 12
   propriedades.
3. **Tabela e resolução** — uma função pura `sideButtonVirtualKey(label)` com o
   mapa rótulo → VK (`1`–`9` = `0x31`–`0x39`, `0` = `0x30`, `-` = `VK_OEM_MINUS`,
   `=` = `VK_OEM_PLUS`, `F13`–`F24` = `0x7C`–`0x87`, `Numpad 0`–`9` = `0x60`–`0x69`)
   e `sideButtonSelections()` devolvendo as 12 globais em ordem, explicitamente,
   sem reflexão sobre `globalThis`.
4. **`processMouseMacros()`** — no topo, antes dos dois switches existentes:
   `"Keypad N"` resolve a VK configurada e emite
   `keyboard.sendHid(vk, {released : !state})`, com `return`. `None` engole o
   evento; `SignalRGB macro` não intercepta e deixa o código original seguir.

Nada mais do arquivo é tocado: `Render()`, `UpdateRGB()`, DPI, bateria e polling
rate ficam idênticos ao upstream.

Uma quinta edição, pequena, é necessária para o teste e também deixa o plugin
mais robusto: mover `macroInputArray.setCallback(...)` de dentro de
`Initialize()` para o escopo do módulo. Ela não depende de nada que
`Initialize()` produza, e no escopo do módulo o caminho de entrada fica
exercitável sem falsificar todo o handshake Bragi.

### Testes

- **Unitário**, sem hardware: toda opção de combobox resolve para uma VK válida;
  os padrões são `1`..`=`; `None` e `SignalRGB macro` se comportam como descrito.
- **Integração** sob Node, no molde de `tests/test_plugin_frames.py`: o harness
  injeta relatórios de notificação roteirizados (`00 01 02 <bitmask LE32>`, os
  mesmos bytes que `tools/scimitar_input_probe.py` capturou no hardware) via o
  `device.read` stubado, roda `Render()` e confere a sequência de `sendHid`:
  press e release na ordem certa, um evento por transição de bit, nada emitido
  quando o bitmask não muda.
  O ponto de risco aqui é `processMacroInputs` precisar de um filho do dongle
  para resolver `keymapType`/`buttonMap`. Se montar esse estado pelo caminho
  público exigir falsificar o handshake inteiro, o teste passa a dirigir
  `processMacroInputs` por um seam de teste explicitamente marcado, e digo isso
  no relato em vez de silenciosamente testar menos.

## Verificação no hardware

1. `python tools/scimitar_input_probe.py 20` — pressionar os 12 botões e
   registrar qual bit corresponde a qual botão físico, confirmando a ordem
   5→botão 1 … 16→botão 12 antes de confiar no padrão.
2. Adicionar o add-on em Settings → Add-ons e conferir no log mais novo em
   `%LOCALAPPDATA%\WhirlwindFX\SignalRgb\Logs\` a linha esperada:
   `HID plugin with id 0x1B1C:0x2B00 already exists. Overwriting with new path: .../cache/addons/...`
3. Com um editor de texto em foco, pressionar os 12 botões: sair `1234567890-=`,
   um caractere por botão, sem repetição nem tecla presa.
4. Trocar efeito e brilho no SignalRGB e repetir o passo 3 — os botões precisam
   continuar iguais, que é o critério de "mexer no RGB não mexe nos botões".
5. Conferir que K70 MAX e MM700 seguem normais, provando que o override ficou
   restrito ao `0x2B00`.
6. `python -m pytest tests/` para a suíte, e
   `python tools/vendor_bragi.py --check` para provar que o fork versionado
   corresponde ao upstream instalado mais o patch.

## Fora de escopo

Com o SignalRGB fechado os botões não funcionam, porque é ele quem mantém o
modo software — foi a escolha feita. Se um dia isso incomodar, o caminho é um
agente Windows independente lendo a mesma interface 2 e injetando por
`SendInput`, espelhando `ScimitarInputMapper` do `mac-agent/agent.cpp`.

## Resultado

Implementado e funcionando no hardware, com uma premissa do plano corrigida.

**O que mudou:** o plano instalava o fork como add-on. Isso não funciona.
SignalRGB mantém três fontes de plugin — a pasta do app, o cache
`plugin_cdn` e os add-ons — e para um mesmo `VID:PID` **vence o último
varrido**. Add-ons adicionados por URL são varridos *antes* dos plugins que o
SignalRGB distribui: o fork registrava `1b1c:2b00` e a cópia oficial tomava de
volta na linha seguinte do log do crawler. Só a pasta de plugins do app é
varrida tarde o bastante para vencer, e é lá que
`tools/vendor_bragi.py --install` escreve, como
`ZZZ_Corsair_Bragi_Scimitar.js`. Uma atualização do SignalRGB cria uma pasta
`app-<versão>` nova e deixa a cópia para trás, então o `--install` é rerodado
depois dela.

Também não é varrida `Documents\WhirlwindFX\Plugins`, apesar de `Effects`,
`Components` e `LCDFaces` ali funcionarem.

**Confirmado no log**, com o K70 MAX seguindo no plugin oficial:

```text
HID plugin with id 0x1B1C:0x2B00 already exists. Overwriting with new path:
  ...\Signal-x64\Plugins\ZZZ_Corsair_Bragi_Scimitar.js
Corsair Bragi Device (Scimitar side buttons) - Wireless Dongle detected!
Corsair Bragi Device - Device is not a wireless dongle. Setting up Wired Mode...
```

O repositório publicado em
[`drungrin/signalrgb-corsair-bragi-scimitar`](https://github.com/drungrin/signalrgb-corsair-bragi-scimitar)
serve para distribuição, não para ser adicionado em Settings → Add-ons; o
README de lá explica isso.

**Desativar o dispositivo corta só a iluminação.** Confirmado no hardware: com
o dongle desativado no SignalRGB os botões continuam funcionando. Como a única
origem das teclas é o `readDeviceNotifications()` no topo do `Render()`, isso
prova que o `Render()` segue sendo chamado — então "botões sem RGB" é uma
configuração válida. Vale lembrar que não existe um dispositivo de fábrica ao
lado do nosso para desativar: é um plugin por `VID:PID`, e o dongle que aparece
é o do fork.
