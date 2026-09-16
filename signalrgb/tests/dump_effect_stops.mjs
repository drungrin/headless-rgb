// Run a SignalRGB effect outside SignalRGB and print, as JSON, everything it
// draws. tests/test_watercolor_effect.py and tests/test_borderlands_effect.py
// compare those gradients against this project's Python renderers.
//
//   node signalrgb/tests/dump_effect_stops.mjs signalrgb/effects/watercolor.html
//
// Scenarios arrive on stdin as JSON:
//
//   {"scenarios": {"defaults": {"globals": {"spread": 25}, "times": [1758000000]}}}
//
// An effect is a plain HTML file with no module system, so this extracts its
// <script> block and runs it in a vm context against fake DOM objects, the way
// SignalRGB's Ultralight runtime would.

import { readFileSync } from "node:fs";
import vm from "node:vm";

const effectPath = process.argv[2];
if (!effectPath) {
	throw new Error("usage: dump_effect_stops.mjs <effect.html>");
}
const html = readFileSync(effectPath, "utf8");
const request = JSON.parse(readFileSync(0, "utf8"));

// --- what SignalRGB reads out of the file ----------------------------------

function parseMeta() {
	const properties = [];
	const pattern = /<meta\s+([^>]*?)\/?>/g;
	let match;
	while ((match = pattern.exec(html)) !== null) {
		const attributes = {};
		const attribute = /(\w+)="([^"]*)"/g;
		let pair;
		while ((pair = attribute.exec(match[1])) !== null) {
			attributes[pair[1]] = pair[2];
		}
		if (attributes.property !== undefined) {
			properties.push(attributes);
		}
	}
	return properties;
}

function parseCanvas() {
	const match = /<canvas[^>]*width="(\d+)"[^>]*height="(\d+)"/.exec(html);
	return match === null
		? null
		: { width: Number(match[1]), height: Number(match[2]) };
}

function parseScript() {
	const match = /<script>([\s\S]*?)<\/script>/.exec(html);
	if (match === null) {
		throw new Error("the effect has no <script> block");
	}
	return match[1];
}

const META = parseMeta();
const CANVAS = parseCanvas();
const SCRIPT = parseScript();
const TITLE = (/<title>([^<]*)<\/title>/.exec(html) || [, null])[1];

// --- fakes ------------------------------------------------------------------

function makeContext(record) {
	return {
		fillStyle: null,
		createLinearGradient(x0, y0, x1, y1) {
			const gradient = { x0, y0, x1, y1, stops: [] };
			record.gradients.push(gradient);
			return {
				__gradient: gradient,
				addColorStop(offset, color) {
					gradient.stops.push({ offset, color });
				},
			};
		},
		fillRect(x, y, w, h) {
			// Record what was painted and with which fill. A gradient built but
			// never used would otherwise pass every other assertion.
			const style = this.fillStyle;
			record.fills.push({
				x, y, width: w, height: h,
				gradient: style && style.__gradient
					? record.gradients.indexOf(style.__gradient)
					: null,
				solid: typeof style === "string" ? style : null,
			});
		},
		beginPath() {},
		rect() {},
		fill() {},
	};
}

function render({ globals, times }) {
	const record = { gradients: [], fills: [] };
	const context = makeContext(record);
	const reads = new Set();

	let frameCallback = null;
	const sandbox = Object.assign({}, globals, {
		Math,
		Date: { now: () => sandbox.__nowMs },
		console: { log() {}, clear() {} },
		document: {
			getElementById() {
				return {
					getContext: () => context,
					width: CANVAS.width,
					height: CANVAS.height,
				};
			},
		},
		window: {
			requestAnimationFrame(callback) {
				frameCallback = callback;
			},
		},
		__nowMs: times[0] * 1000,
	});

	// `has` returning true routes every bare identifier through `get`, so this
	// also records which globals the script reads. A <meta property> the script
	// never looks at is a dead setting in the SignalRGB UI.
	const proxied = new Proxy(sandbox, {
		has: () => true,
		get(target, key) {
			if (typeof key === "string") {
				reads.add(key);
			}
			return target[key];
		},
	});

	vm.runInNewContext(SCRIPT, vm.createContext(proxied));
	if (frameCallback === null) {
		throw new Error("the effect never asked for a frame");
	}

	const frames = [];
	for (const seconds of times) {
		sandbox.__nowMs = seconds * 1000;
		const firstGradient = record.gradients.length;
		const firstFill = record.fills.length;
		frameCallback();
		frames.push({
			elapsed: seconds,
			// Every gradient and fill this one frame produced, in order. An
			// effect with per-lane bands makes several of each.
			gradients: record.gradients.slice(firstGradient),
			fills: record.fills.slice(firstFill).map((fill) =>
				Object.assign({}, fill, {
					gradient: fill.gradient === null ? null : fill.gradient - firstGradient,
				})
			),
		});
	}

	return { globals, frames, reads: Array.from(reads).sort() };
}

const output = {
	title: TITLE,
	canvas: CANVAS,
	meta: META,
	scenarios: {},
};
for (const [name, scenario] of Object.entries(request.scenarios)) {
	output.scenarios[name] = render(scenario);
}

process.stdout.write(JSON.stringify(output));
