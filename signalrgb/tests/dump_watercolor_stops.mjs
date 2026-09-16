// Run signalrgb/effects/watercolor.html outside SignalRGB and print, as JSON,
// the gradient it builds. tests/test_watercolor_effect.py compares every stop
// against headless_lights.effects.watercolor_color, which is what keeps the
// SignalRGB effect and the Python and C++ renderers from drifting apart.
//
//   node signalrgb/tests/dump_watercolor_stops.mjs
//
// The effect is a plain HTML file with no module system, so this extracts its
// <script> block and runs it in a vm context against fake DOM objects, the way
// SignalRGB's Ultralight runtime would.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const HERE = dirname(fileURLToPath(import.meta.url));
const EFFECT = join(HERE, "..", "effects", "watercolor.html");
const html = readFileSync(EFFECT, "utf8");

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
			// Record what was actually painted, and with which fill. A gradient
			// built but never used would otherwise pass every other assertion.
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

// Renders `times.length` frames, one per stubbed Unix timestamp, and returns
// everything the effect drew.
function render({ spread, tilt, times }) {
	const record = { gradients: [], fills: [] };
	const context = makeContext(record);
	const reads = new Set();

	let frameCallback = null;
	const sandbox = {
		spread,
		tilt,
		Math,
		Date: { now: () => sandbox.__nowMs },
		console: { log() {}, clear() {} },
		document: {
			getElementById() {
				return { getContext: () => context, width: CANVAS.width, height: CANVAS.height };
			},
		},
		window: {
			requestAnimationFrame(callback) {
				frameCallback = callback;
			},
		},
		__nowMs: times[0] * 1000,
	};

	// `has` returning true routes every bare identifier through `get`, so this
	// also records which globals the script actually reads. A <meta property>
	// the script never looks at is a dead setting in the SignalRGB UI.
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
		const before = record.gradients.length;
		frameCallback();
		frames.push({
			elapsed: seconds,
			gradient: record.gradients[record.gradients.length - 1] || null,
			newGradients: record.gradients.length - before,
			fill: record.fills[record.fills.length - 1] || null,
		});
	}

	return {
		spread,
		tilt,
		frames,
		reads: Array.from(reads).sort(),
	};
}

// --- scenarios --------------------------------------------------------------

// A fixed, arbitrary Unix time and the same instant 12 s later: 12 s is the
// effect's own drift period (elapsed / 12.0), so the two frames must differ.
const T0 = 1758000000;

process.stdout.write(
	JSON.stringify({
		title: TITLE,
		canvas: CANVAS,
		meta: META,
		defaults: render({
			spread: Number(META.find((entry) => entry.property === "spread").default),
			tilt: Number(META.find((entry) => entry.property === "tilt").default),
			times: [T0, T0 + 12],
		}),
		flat: render({ spread: 25, tilt: 0, times: [T0] }),
		wide: render({ spread: 60, tilt: 17, times: [T0] }),
		narrow: render({ spread: 5, tilt: 40, times: [T0] }),
	}),
);
