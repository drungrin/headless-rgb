# mac-agent

Tooling for the agent that applies static colours and PC-defined effects on the
Mac.

`icue_probe.cpp` remains only as a historical diagnostic of the SDK. The
production agent neither loads iCUE nor redistributes its framework.

`k70max_probe.cpp`, `k70max_layout.h` and `mm700_probe.cpp` validate the direct
HID backends. The protocol and the physical map derive from the OpenLinkHub K70
MAX and MM700 implementations respectively; those files and `agent.cpp` are
distributed under GPL-3.0-or-later.

`g560_probe.cpp` enumerates the Logitech G560 Lightsync interface. With
`--color RRGGBB` it applies the same colour to all four zones using the HID
protocol documented in the
[OpenRGB](https://gitlab.com/CalcProgrammer1/OpenRGB/-/tree/master/Controllers/LogitechController)
driver. That file is provided under GPL-2.0-or-later, matching the licence of
the reference implementation.

`scimitar_probe.cpp` exercises the non-exclusive HID fallback for the Scimitar
Elite Wireless SE through the Slipstream receiver (`1b1c:2b00`, endpoint `09`).
With no arguments it only enumerates; with `--color RRGGBB` it uses software
mode, the RGB endpoint and a colour write. The format comes from OpenLinkHub and
the file is GPL-3.0-or-later.

`scimitar_input_probe.cpp` documents the 12-side-button bitmask received on
Slipstream interface 2. The agent turns those bits into `1` through `=` via
CoreGraphics, without observing the K70 keys.

`agent.cpp` combines four direct HID backends and listens on `127.0.0.1:7531`
only. The PC sends `COLOR RRGGBB`, `EFFECT WATERCOLOR`, `EFFECT STRANGER-THINGS`,
`EFFECT BORDERLANDS-4` or `STATUS` through an SSH session. By default the binary
drives the K70 MAX, MM700, Scimitar and G560.

## Streaming port (127.0.0.1:7532)

Besides the command port, the agent accepts per-LED frames on
`127.0.0.1:7532`. That is where SignalRGB, running on the PC, paints the four
peripherals. This port is loopback as well: the PC reaches it through an SSH
tunnel (`ssh -N -L 7532:127.0.0.1:7532`), so nothing new is exposed on the
network.

The protocol lives in [`stream_protocol.h`](stream_protocol.h). Each frame is a
6-byte header followed by RGB triples, little-endian:

| offset | bytes | field | value |
| --- | --- | --- | --- |
| 0 | 1 | magic0 | `0x53` (`'S'`) |
| 1 | 1 | magic1 | `0x47` (`'G'`) |
| 2 | 1 | version | `0x01` |
| 3 | 1 | device | 0 = K70, 1 = MM700, 2 = G560, 3 = Scimitar |
| 4 | 2 | length | uint16 LE, always `3 × LEDs` for that device |
| 6 | length | payload | R, G, B per LED |

| device | id | LEDs | payload | frame |
| --- | --- | --- | --- | --- |
| K70 MAX | 0 | 142 | 426 | 432 |
| MM700 | 1 | 3 | 9 | 15 |
| G560 | 2 | 4 | 12 | 18 |
| Scimitar | 3 | 3 | 9 | 15 |

The K70 receives all **142 hardware channels**, not the 116 that light up: the
frame index is the index into `kK70LedCoordinates`, and channels with no
physical LED arrive black. That way the agent needs no mapping table of its own
— the client owns the map, and it already needs one to position the keys.

`length` is validated against the exact constant for the device, which turns the
header into a five-field discriminator and allows resynchronising in the middle
of a corrupted stream. There is no checksum (TCP already has one) and **the agent
never writes to this port**: it is a one-way stream, with no ACK and no
handshake.

Agent behaviour:

- **Coalescing** — only the newest frame per device is applied. A device that
  cannot keep up drops frames instead of accumulating latency.
- **Pacing** — a minimum interval per device (K70 33 ms, MM700 and Scimitar
  16 ms, G560 40 ms). The K70 costs four blocking HID round trips per frame.
- **Fallback** — after 3 s without frames, the device returns to the local
  effect configured in the LaunchAgent.
- **Precedence** — a `COLOR` or `EFFECT` on port 7531 reclaims all four devices
  immediately, so every `k70=ok` in the response corresponds to a real write.
  Streaming resumes on the next frame.

The payload is binary: anyone writing a client must send **bytes**, never a text
string. Measured in this project: a 256-byte ramp sent as a string only survives
if the runtime encodes it as latin1; in UTF-8 every byte `>= 0x80` becomes two
(`0x80` → `c2 80`), which would turn a 432-byte K70 frame into as much as 640
bytes of garbage. That is exactly why the plugin builds an array of integers.

To exercise this port without SignalRGB, from the PC:

```sh
headless-lights mac-stream --effect watercolor
headless-lights mac-stream --color ff6600 --device k70
```

`--effect watercolor` runs locally the same temporal renderer used on the PC,
with a per-key gradient on the K70 and independent per-zone samples on the
MM700, G560 and Scimitar. Unix time keeps the phase aligned across both
machines.

`--effect stranger-things` reproduces only the ambient animation from the
profile, without key capture or reactive layers.

`--effect borderlands-4` reproduces the continuous red, orange and gold layers
from the profile, without the original key-triggered effects.

## Building and installing

To compile, install and load the LaunchAgent on the Mac:

```sh
sh install.sh
```

If `/usr/bin/clang++` is blocked by the Xcode licence (the error mentions
`sudo xcodebuild -license`), the installer automatically falls back to the
Command Line Tools compiler, which carries no such requirement. Accepting the
licence also works, but needs an interactive terminal.

The installer uses Homebrew's `hidapi`, writes the files under
`~/Library/Application Support/headless-lights` and installs
`~/Library/LaunchAgents/com.headless-lights.agent.plist`. The process starts on
Borderlands 4, restarts if a device is reconnected, and exposes no network port.

## Tests

The C++ checks run on any platform:

```sh
sh mac-agent/tests/run.sh
```

They compile and run the frame parser tests in `tests/stream_protocol_test.cpp`,
then type-check `agent.cpp` with `-Wall -Wextra -Werror`. On macOS the
type-check uses the real hidapi and ApplicationServices headers. Elsewhere it
uses the declaration-only stubs in `tests/shims/`, which reproduce the real
hidapi, ApplicationServices and BSD socket signatures, so the agent can be
type-checked from the Windows PC where the SignalRGB plugin is developed.

The stubs catch wrong argument types, wrong arity and calls that do not exist.
They cannot catch linking or runtime behaviour, so `install.sh` on the Mac
remains the final word.

## Accessibility

The Scimitar must stay in software mode to accept animated RGB. In that mode the
agent reads the side-button bitmask from the Slipstream vendor interface and
publishes `1` through `=` via CoreGraphics. Grant Accessibility to the final
executable only:

```text
~/Library/Application Support/headless-lights/bin/headless-lights-agent
```

Do not grant Accessibility to `sshd-keygen-wrapper`.

Because the installer uses an ad-hoc signature, TCC keys the binary by its
CDHash. Rebuilding from changed source changes that hash and **invalidates the
grant**, even though the entry still appears in the list — the symptom is
`scimitar=error` together with `scimitar-buttons=unavailable`. When that
happens, remove and re-add `headless-lights-agent` in the Accessibility list,
then restart the agent:

```sh
launchctl kickstart -k gui/$(id -u)/com.headless-lights.agent
```

Rebuilding the *same* source reproduces the same hash and preserves the grant.
At the end, `install.sh` reports whether the permission is active
(`accessibility: granted`), so a break shows up immediately instead of surfacing
later as `scimitar=error`.

The Scimitar backend treats an empty response as a timeout and invalidates the
RGB endpoint. When the mouse returns to wireless, the agent retries after two
seconds, restores the animation and keeps the side-button mapping active.
