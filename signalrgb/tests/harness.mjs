// Minimal stand-ins for the SignalRGB globals, so the plugin's frame building
// can be exercised outside the app. Only what headless-lights-mac.js touches.

export function installGlobals({ model, canvas, lightingMode = "Canvas", forcedColor = "#009bde" }) {
	const sent = [];
	const logs = [];

	globalThis.controller = { model, ip: "127.0.0.1", port: 7532 };
	globalThis.LightingMode = lightingMode;
	globalThis.forcedColor = forcedColor;

	globalThis.device = {
		name: null,
		size: null,
		ledNames: null,
		ledPositions: null,
		setName(value) { this.name = value; },
		setSize(value) { this.size = value; },
		setControllableLeds(names, positions) {
			this.ledNames = names;
			this.ledPositions = positions;
		},
		setImageFromUrl() {},
		addFeature() {},
		log(message) { logs.push(String(message)); },
		// canvas is a function (x, y) -> [r, g, b]
		color(x, y) { return canvas(x, y); },
	};

	globalThis.tcp = {
		createSocket() {
			return {
				ConnectedState: 3,
				state: 3,
				on() {},
				connect() {},
				close() {},
				send(data) { sent.push(data); },
			};
		},
	};
	globalThis.udp = {
		send(host, port, data) {
			if (host !== "127.0.0.1" || port !== 7532) {
				throw new Error(`unexpected UDP target ${host}:${port}`);
			}
			sent.push(data);
		},
	};

	globalThis.service = {
		controllers: [],
		log() {},
		getController() { return undefined; },
		addController(instance) { this.controllers.push({ obj: instance }); },
		updateController() {},
		announceController() {},
	};

	return { sent, logs };
}
