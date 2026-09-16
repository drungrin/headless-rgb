// Stand-ins for the SignalRGB globals the Beelight plugin touches, plus a fake
// serial port that records every write and replays scripted reads.
//
// The scripted reads are produced by the *Python* encoder in
// tests/test_beelight_plugin_frames.py, so the JS handshake is driven by real
// frames rather than by its own encoder answering itself.

export function hexToBytes(hex) {
	const bytes = new Array(hex.length / 2);
	for (let index = 0; index < bytes.length; index++) {
		bytes[index] = parseInt(hex.substr(index * 2, 2), 16);
	}
	return bytes;
}

export function bytesToHex(bytes) {
	let hex = "";
	for (let index = 0; index < bytes.length; index++) {
		hex += (bytes[index] & 0xff).toString(16).padStart(2, "0");
	}
	return hex;
}

// The Mac plugin taught this the hard way: a frame handed to the engine as a
// string is re-encoded, and every byte >= 0x80 becomes two. Fail loudly here
// rather than let a regression reach the strip.
function requireByteArray(data) {
	if (!Array.isArray(data)) {
		throw new Error(`Serial.write got ${typeof data}, expected an array of bytes`);
	}
	for (let index = 0; index < data.length; index++) {
		const byte = data[index];
		if (!Number.isInteger(byte) || byte < 0 || byte > 255) {
			throw new Error(`Serial.write byte ${index} is ${byte}, not a uint8`);
		}
	}
}

// `responses` maps a 1-based write index to the frames the device answers with.
// Queuing only after the matching write is what makes the plugin's drain read,
// which happens *before* each acknowledged write, behave as it does on hardware.
export function makeSerial({ responses = {}, failConnect = false } = {}) {
	const writes = [];
	let inbox = [];
	let connected = false;
	let connectOptions = null;

	return {
		writes,
		get connectOptions() { return connectOptions; },
		get isOpen() { return connected; },
		connect(options) {
			connectOptions = options;
			if (failConnect) {
				return false;
			}
			connected = true;
			return true;
		},
		disconnect() {
			connected = false;
			inbox = [];
			return true;
		},
		isConnected() { return connected; },
		write(data) {
			requireByteArray(data);
			writes.push(bytesToHex(data));
			const scripted = responses[writes.length];
			if (scripted) {
				for (const frame of scripted) {
					inbox = inbox.concat(hexToBytes(frame));
				}
			}
			return true;
		},
		read(maxBytes) {
			if (inbox.length === 0) {
				// The real API returns null on timeout, not an empty array.
				return null;
			}
			const taken = inbox.slice(0, maxBytes);
			inbox = inbox.slice(maxBytes);
			return taken;
		},
		getDeviceInfo() { return { vid: 0x2e3c, pid: 0x5740 }; },
		availablePorts() { return ["COM3"]; },
	};
}

export function installGlobals({
	serial,
	canvas,
	lightingMode = "Canvas",
	forcedColor = "#009bde",
	shutdownColor = "#000000",
}) {
	const logs = [];

	globalThis.__beelightSerial = serial;
	globalThis.LightingMode = lightingMode;
	globalThis.forcedColor = forcedColor;
	globalThis.shutdownColor = shutdownColor;

	globalThis.device = {
		name: null,
		size: null,
		ledNames: null,
		ledPositions: null,
		frameRateTargets: [],
		setName(value) { this.name = value; },
		setSize(value) { this.size = value; },
		setControllableLeds(names, positions) {
			this.ledNames = names;
			this.ledPositions = positions;
		},
		setFrameRateTarget(value) { this.frameRateTargets.push(value); },
		setImageFromUrl() {},
		addFeature() {},
		// Anything worth logging must reach the log file, so the second
		// argument is part of the contract, not decoration.
		log(message, options) {
			if (!options || options.toFile !== true) {
				throw new Error(`device.log without toFile: ${message}`);
			}
			logs.push(String(message));
		},
		color(x, y) { return canvas(x, y); },
	};

	return { logs };
}

// Date.now() drives the plugin's own frame pacing, so a second Render in the
// same millisecond is correctly skipped. Burn real time when a scenario needs
// two frames to actually go out.
export function busyWait(milliseconds) {
	const until = Date.now() + milliseconds;
	while (Date.now() < until) {
		// Intentionally empty.
	}
}
