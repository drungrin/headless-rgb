// Drive the Bragi fork's input path under Node and print, as JSON, every key it
// would send. tests/test_bragi_side_buttons.py asserts against the output, so a
// regression in the bit window, the keymap or the virtual keys fails there
// instead of on the mouse.

import {
	DEFAULT_KEYS,
	SIDE_BUTTON_COUNT,
	installGlobals,
	macroReport,
	sideButtonBit,
} from "./bragi_harness.mjs";

const SCIMITAR_SE = 0x2b22;

// A fresh module per scenario: the plugin keeps module-level state, and the
// bit diffing is the whole point of these tests.
let instance = 0;
async function load() {
	return import(`../corsair-bragi-scimitar.js?instance=${instance++}`);
}

async function scenario({ sideButtons, masks }) {
	const globals = installGlobals(sideButtons ? { sideButtons } : {});
	const plugin = await load();
	plugin.__installMacroSubdeviceForTest(1, SCIMITAR_SE);

	for (const mask of masks) {
		plugin.__processInputForTest(macroReport(mask));
	}

	return globals;
}

const output = {};

// Each side button, pressed then released on its own.
{
	const masks = [];
	for (let number = 1; number <= SIDE_BUTTON_COUNT; number++) {
		masks.push(sideButtonBit(number), 0);
	}
	const { sentKeys, macroEvents } = await scenario({ masks });
	output.eachButton = { sentKeys, macroEventCount: macroEvents.length };
}

// Two buttons held together, then released one at a time: one event per bit
// that actually changed, and nothing at all for a repeated mask.
{
	const first = sideButtonBit(1);
	const third = sideButtonBit(3);
	const { sentKeys } = await scenario({
		masks: [first, first | third, first | third, third, 0],
	});
	output.overlapping = { sentKeys };
}

// A remapped button, "None", and the fall-through to SignalRGB's macro engine.
{
	const custom = [...DEFAULT_KEYS];
	custom[0] = "F13";
	custom[1] = "None";
	custom[2] = "SignalRGB macro";
	const { sentKeys, macroEvents } = await scenario({
		sideButtons: custom,
		masks: [
			sideButtonBit(1), 0,
			sideButtonBit(2), 0,
			sideButtonBit(3), 0,
		],
	});
	output.remapped = {
		sentKeys,
		macroEvents: macroEvents.map((event) => ({
			from: event.from,
			type: event.type,
			name: event.data.name,
			released: event.data.released,
		})),
	};
}

// Bits outside the side-button window must not become key presses: bit 3 is the
// DPI cycle, and bit 17 is past the last side button.
{
	const { sentKeys } = await scenario({ masks: [1 << 3, 0, 1 << 17, 0] });
	output.outsideWindow = { sentKeys };
}

// The defaults every button starts on, read back from the plugin's own settings.
{
	const plugin = await load();
	output.defaults = plugin.ControllableParameters()
		.filter((parameter) => parameter.property.startsWith("sideButton"))
		.map((parameter) => ({
			property: parameter.property,
			default: parameter.default,
			values: parameter.values,
		}));
	output.productId = plugin.ProductId();
}

process.stdout.write(JSON.stringify(output, null, "\t"));
