# headless-lights

A headless RGB controller for a desk split across three machines. It drives the
lighting on a Linux PC directly, controls the peripherals attached to a Mac over
an authenticated SSH connection, and lets SignalRGB on a Windows PC paint both
those Mac peripherals and the Beelight strip, per LED.

It exists to keep lighting consistent without depending on a vendor GUI: no
iCUE, and no graphical session required on the Linux side.

## Features

- **Beelight V3/AT32** USB strip control, including static colour and per-LED frames.
- Corsair RGB memory, ASUS Aura controllers and Corsair iCUE LINK fans through OpenRGB.
- Animated effects kept in phase across Linux and macOS by the Unix clock:
  - Watercolor Spectrum
  - Stranger Things
  - Borderlands 4
- systemd user services for persistent static colour or effects.
- A C++ macOS agent that drives peripherals over direct HID, without the iCUE SDK.
- Automatic Scimitar wireless recovery after a disconnect or timeout.
- Two SignalRGB add-ons: one streams the Windows canvas to the Mac peripherals
  per LED, the other drives the Beelight strip straight from Windows.

This project controls lighting only. It never changes fan speeds, thermal curves
or any other cooling parameter.

## Tested hardware

| Platform | Device | Support |
| --- | --- | --- |
| Linux | Beelight V3/AT32 (`2e3c:5740`) | 33 pixels, direct serial protocol |
| Windows | Beelight V3/AT32 (`2e3c:5740`) | the same strip, through the SignalRGB add-on |
| Linux | Corsair Vengeance RGB DDR5 | 12 LEDs per module, through OpenRGB |
| Linux | ASUS PRIME Z690-P Aura (`0b05:19af`) | Asiahorse Lightsaber-X, 24 LEDs on `Aura Addressable 2` |
| Linux | Corsair iCUE LINK System Hub (`1b1c:0c3f`) | six LX120/LX120-R/LX140-R fans, 18 LEDs per fan |
| macOS | Corsair K70 MAX | 116 lit keys (142 hardware channels), direct HID |
| macOS | Corsair MM700 RGB | 3 zones, direct HID |
| macOS | Corsair Scimitar Elite Wireless SE | 3 RGB zones and 12 side buttons |
| macOS | Logitech G560 | 4 zones, direct HID |

Support is validated for this specific combination. Other models may work, but
are not guaranteed.

## Repository layout

| Path | Contents |
| --- | --- |
| `src/headless_lights/` | Python package and the `headless-lights` CLI |
| `mac-agent/` | C++ agent for macOS, its wire protocol and its test suite |
| `signalrgb/` | Canonical source of both SignalRGB add-ons, plus their test harnesses |
| `windows/` | SSH tunnel supervisor and the UDP-to-TCP bridge |
| `tools/` | K70 layout generator, add-on sync and stream capture helpers |
| `udev/` | udev rule scoping direct access to the identified controllers |
| `tests/` | Python test suite |

## Requirements

### Linux

- Python 3.11 or newer
- OpenRGB
- `i2c-tools` for the DDR5 memory
- membership in the `dialout`, `i2c` and `plugdev` groups

On Debian or Ubuntu based distributions:

```bash
sudo apt install openrgb i2c-tools
sudo usermod -aG dialout,i2c,plugdev "$USER"
```

Log out and back in (or reboot) so the new groups take effect.

### macOS (optional)

The Mac agent needs Homebrew's `hidapi` and Accessibility permission for the
final binary. Full instructions are in [`mac-agent/README.md`](mac-agent/README.md).

### Windows (optional, for SignalRGB)

- SignalRGB
- Python 3.11 or newer
- an SSH key already authorised on the Mac, for the Mac peripherals only

The Beelight add-on needs neither Python nor SSH: SignalRGB opens the strip's
serial port itself.

## Installation

From the repository root, install the package into a virtual environment:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

The bundled udev rule scopes direct access to the ASUS Aura and Corsair iCUE
LINK controllers this project identifies. Install it from the repository root:

```bash
sudo install -o root -g root -m 0644 \
  udev/99-headless-lights-asus-aura.rules \
  /etc/udev/rules.d/99-headless-lights-asus-aura.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=hidraw --action=add
```

## Quick start

### Beelight strip

Discover the serial port and query the device:

```bash
headless-lights detect
headless-lights info
```

Apply a colour, a brightness level, or a frame carrying one colour per LED:

```bash
headless-lights color ff6600
headless-lights brightness 60
headless-lights pixels COLOR_01 COLOR_02 ... COLOR_33
```

Use `hold` to keep the port open, answer the device heartbeats, and restore the
lighting if the USB connection comes back:

```bash
headless-lights hold 0000ff --fps 20
```

To apply a static colour at login:

```bash
headless-lights service-install 0000ff --fps 20
```

### Local OpenRGB devices

```bash
# Corsair memory
headless-lights ram-color 0000ff
headless-lights ram-service-install 0000ff

# Asiahorse strip wired to the ASUS Aura controller
headless-lights aura-color 0000ff
headless-lights aura-service-install 0000ff

# Corsair iCUE LINK fans
headless-lights hub-hold 0000ff
headless-lights hub-service-install 0000ff
```

The controller validates the topology before writing: the tested configuration
expects six zones of 18 LEDs (108 LEDs total) on the hub, and 24 LEDs on the
configured Aura zone.

### Animated effects

Effects use Unix time as a shared phase, so the Linux and macOS animations stay
aligned.

| Effect | Description |
| --- | --- |
| `watercolor` | Soft bands of cyan, blue, violet, magenta, pink and pale yellow |
| `stranger-things` | Blue/purple base with red rain and waves, plus periodic flashes |
| `borderlands-4` | Continuous red, orange and gold layers |

Run a temporary preview:

```bash
headless-lights effect-preview watercolor --scope hub --seconds 20 --fps 12
headless-lights effect-preview stranger-things --scope pc --seconds 21 --fps 12
headless-lights effect-preview borderlands-4 --scope pc --seconds 20 --fps 12
```

Hold an effect in the foreground, or install it as a service:

```bash
headless-lights effect-hold watercolor --scope pc --fps 12
headless-lights effect-service-install borderlands-4 --scope pc --fps 12
```

The available scopes are:

| Scope | Devices |
| --- | --- |
| `hub` | iCUE LINK fans |
| `local` | Fans, Corsair memory and Asiahorse |
| `pc` | Fans, Corsair memory, Asiahorse and Beelight |

Do not run a preview or a static colour against a device while the effect
service is driving it. Pause the renderer before changing the lighting
temporarily:

```bash
systemctl --user stop headless-lights-effect.service
# Run the command you need.
systemctl --user start headless-lights-effect.service
```

## macOS agent

The agent in [`mac-agent/`](mac-agent/) drives the K70 MAX, MM700, Scimitar and
G560 over direct HID. It listens on `127.0.0.1` only; other machines reach it
through an authenticated SSH connection.

Install it on the Mac with:

```bash
cd mac-agent
sh install.sh
```

Then, from the Linux PC, use a configured SSH destination:

```bash
headless-lights mac-status --host user@mac.local
headless-lights mac-color 0000ff --host user@mac.local
headless-lights mac-effect stranger-things --host user@mac.local
```

The Scimitar must stay in software mode to accept animated RGB. In that mode the
agent restores the 12 side buttons as the keys `1` through `=`, which is why it
needs Accessibility permission for the agent binary alone.

## SignalRGB on Windows

Besides the command port, the agent accepts **per-LED frames** on
`127.0.0.1:7532`. That is how SignalRGB, running on a Windows PC, takes over the
four Mac peripherals: the K70 appears on the canvas with real key geometry, and
the others as zones.

SignalRGB 2.5 exposes UDP, but not TCP, to third-party plugins, so a small local
bridge validates each datagram and forwards the same bytes into the SSH tunnel.
TCP and UDP share port number 7532 without conflict because they are separate
port spaces, and every endpoint stays bound to loopback:

```text
SignalRGB --UDP 7532--> local bridge --TCP 7532/SSH--> Mac agent --> HID
```

One supervisor starts and restarts both the bridge and the tunnel:

```powershell
powershell -ExecutionPolicy Bypass -File windows\start-mac-tunnel.ps1
```

By default it uses the `mac` destination from your `~/.ssh/config`; pass
`-MacHost` for another. To keep it up from logon, register the scheduled task
documented in the script header. It writes diagnostics to
`%LOCALAPPDATA%\headless-lights\`.

The add-on is published at
[`drungrin/signalrgb-mac-bridge`](https://github.com/drungrin/signalrgb-mac-bridge).
Add that repository URL under **Settings → Add-ons**; do not copy the file into
the USB plugin folder, which SignalRGB only scans for HID devices. The canonical
source also stays in [`signalrgb/`](signalrgb/) so the tests and the layout
generator validate it. If `k70max_layout.h` changes, regenerate and verify:

```bash
python tools/gen_k70_layout.py --write
python tools/gen_k70_layout.py --check
```

When SignalRGB stops sending frames — PC asleep, application closed, tunnel down
— each device returns to its local effect after three seconds. A `mac-color` or
`mac-effect` on the command port preempts streaming immediately, and streaming
reclaims the devices on the next frame.

To exercise that port without SignalRGB, which is useful when debugging the
agent:

```bash
headless-lights mac-stream --effect watercolor
headless-lights mac-stream --color ff6600 --device k70 --fps 30
```

The frame format is documented in [`mac-agent/README.md`](mac-agent/README.md).

### Beelight strip on Windows

The strip plugs into the Windows PC, where it enumerates as a plain CDC serial
port. A second add-on speaks the Beelight protocol on that port directly, so it
needs no tunnel, no bridge and no second machine:

```text
SignalRGB --COM port--> Beelight strip
```

It is published at
[`drungrin/signalrgb-beelight`](https://github.com/drungrin/signalrgb-beelight);
add that repository URL under **Settings → Add-ons** as well. The handshake asks
the strip for its own pixel count, so the canvas shows the LEDs the hardware
reports rather than a constant.

SignalRGB holds the serial port exclusively while it runs, so nothing else can
drive the strip at the same time. That is not a regression: the Python CLI has
always needed a POSIX host, and never worked on Windows.

The canonical source stays in [`signalrgb/`](signalrgb/). Publish a change to
either add-on with:

```bash
python tools/sync_addon.py beelight ../signalrgb-beelight
python tools/sync_addon.py mac ../signalrgb-mac-bridge --check
```

Two platform notes: the Python package installs and runs on Windows, but the
Beelight strip commands need a POSIX host, and the `*-service-install` commands
depend on systemd, so both remain the Linux path.

## Services

Installing a service creates a user unit; the controller never needs to run as
root. Check the Linux services with:

```bash
systemctl --user status \
  headless-lights-ram.service \
  headless-lights-aura.service \
  headless-lights-hub.service \
  headless-lights-effect.service
```

## Development and testing

Run the Python suite from the repository root:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

It covers the Beelight protocol, OpenRGB device selection, hub topologies,
systemd units, macOS agent communication, effect rendering, the streaming
protocol, the Windows bridge and the generated K70 layout.

The C++ side has its own suite, which runs on any platform:

```bash
sh mac-agent/tests/run.sh
```

It compiles and runs the frame parser tests and type-checks `agent.cpp` with
`-Wall -Wextra -Werror`. Away from macOS it uses the declaration-only stubs in
`mac-agent/tests/shims/`, which reproduce the real hidapi, ApplicationServices
and BSD socket signatures. Those catch type and arity errors, but they do not
replace the real `install.sh` build on the Mac.

When Node.js is available, the Python suite also runs both SignalRGB add-ons
under Node and decodes the frames they produce with this project's own parsers,
so a plugin and its device cannot drift apart silently:

- `tests/test_plugin_frames.py` renders the Mac add-on against a fake canvas and
  parses the result with the agent's stream protocol.
- `tests/test_beelight_plugin_frames.py` runs the Beelight add-on against a fake
  serial port, feeds it acknowledgements encoded by
  `headless_lights.beelight.protocol`, and decodes everything it writes back
  with that same module.

Without Node.js, those tests are skipped.

## License

This repository does not yet carry a distribution license. Contact the
maintainer before reusing, modifying or redistributing the code. Some files
under [`mac-agent/`](mac-agent/) carry their own attribution and license terms,
documented in that directory's README.
