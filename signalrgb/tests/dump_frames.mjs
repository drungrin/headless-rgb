// Drive the plugin through one Render() per device and print the exact bytes it
// would put on the wire, as hex. Cross-checked against the Python parser by
// tests/test_plugin_frames.py, which is what proves the two ends agree.

import { installGlobals } from "./harness.mjs";

const MODELS = ["k70", "mm700", "g560", "scimitar"];

// Deterministic, position-dependent canvas: every cell gets a distinct colour,
// so a swapped or duplicated LED lookup shows up as wrong bytes.
function canvas(x, y) {
	return [(x * 11 + 3) & 0xff, (y * 29 + 7) & 0xff, (x * 7 + y * 13 + 1) & 0xff];
}

const output = {};
for (const model of MODELS) {
	const { sent } = installGlobals({ model, canvas });
	// Fresh module instance per device: the plugin keeps module-level state.
	const plugin = await import(`../headless-lights-mac.js?model=${model}`);
	plugin.Initialize();
	plugin.Render();
	if (sent.length !== 1) {
		throw new Error(`${model}: expected 1 frame, got ${sent.length}`);
	}
	output[model] = {
		frame: Buffer.from(sent[0]).toString("hex"),
		name: globalThis.device.name,
		size: globalThis.device.size,
		ledCount: globalThis.device.ledNames.length,
	};
}

// Forced mode on the K70: every slot should carry the same colour.
{
	const { sent } = installGlobals({
		model: "k70",
		canvas,
		lightingMode: "Forced",
		forcedColor: "#ff6600",
	});
	const plugin = await import("../headless-lights-mac.js?forced=1");
	plugin.Initialize();
	plugin.Render();
	output["k70_forced"] = { frame: Buffer.from(sent[0]).toString("hex") };
}

// Return the layout in the same JSON document, rather than writing a generated
// file into the source tree during every test run.
{
	installGlobals({ model: "k70", canvas });
	const plugin = await import("../headless-lights-mac.js?layout=1");
	output.layout = {
		wireIndex: plugin.__testLayout.k70.wireIndex,
		ledPositions: plugin.__testLayout.k70.ledPositions,
		intervals: {},
	};
	for (const model of ["k70", "mm700", "g560", "scimitar"]) {
		output.layout.intervals[model] = plugin.__testLayout[model].intervalMs;
		if (model !== "k70") {
			output.layout[model] = plugin.__testLayout[model].ledPositions;
		}
	}
}

process.stdout.write(JSON.stringify(output));
