// Stand-ins for the SignalRGB globals the Bragi fork touches on its input path.
//
// Only that path is driven here. The lighting half of the plugin is upstream's,
// unchanged by tools/vendor_bragi.py, and pretending to be a Bragi dongle well
// enough to exercise it would test the pretence instead.

export const SIDE_BUTTON_COUNT = 12;

// The order the macOS agent uses, which is what the fork defaults to.
export const DEFAULT_KEYS = [
	"1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-", "=",
];

export function installGlobals({ sideButtons = DEFAULT_KEYS } = {}) {
	const sentKeys = [];
	const macroEvents = [];
	const logs = [];

	for (let index = 0; index < SIDE_BUTTON_COUNT; index++) {
		globalThis[`sideButton${index + 1}`] = sideButtons[index];
	}

	globalThis.device = {
		log(message) { logs.push(String(message)); },
		set_endpoint() {},
		addFeature() {},
		pause() {},
		read() { return []; },
		write() {},
		getLastReadSize() { return 0; },
		setSubdeviceSize() {},
		setSubdeviceLeds() {},
		setSubdeviceName() {},
		setSubdeviceImageFromUrl() {},
		removeSubdevice() {},
	};

	globalThis.keyboard = {
		sendHid(vkCode, options) {
			if (!Number.isInteger(vkCode)) {
				throw new Error(`sendHid got ${typeof vkCode}, expected a number`);
			}
			if (options === undefined || typeof options.released !== "boolean") {
				throw new Error("sendHid needs a boolean 'released' option");
			}
			sentKeys.push({ vkCode, released: options.released });
		},
		sendEvent(data, type) { macroEvents.push({ data, type, from: "keyboard" }); },
	};

	globalThis.mouse = {
		sendHid(vkCode, options) { sentKeys.push({ vkCode, released: options.released }); },
		sendEvent(data, type) { macroEvents.push({ data, type, from: "mouse" }); },
	};

	globalThis.battery = {
		setBatteryLevel() {},
		setBatteryState() {},
	};

	return { sentKeys, macroEvents, logs };
}

// The bytes the dongle actually puts on its vendor interface for a macro event,
// as captured by tools/scimitar_input_probe.py: a report id, the subdevice, the
// notification type, then a little-endian bitmask of every held button.
export function macroReport(mask, { subdeviceID = 1 } = {}) {
	const report = new Array(35).fill(0);
	report[0] = 0x00;
	report[1] = subdeviceID;
	report[2] = 0x02;
	for (let byte = 0; byte < 4; byte++) {
		report[3 + byte] = (mask >>> (byte * 8)) & 0xff;
	}
	return report;
}

// Side button N is bit N + 4: bits 5 through 16, the same window the macOS
// agent masks with 0x0001ffe0.
export function sideButtonBit(number) {
	return 1 << (number + 4);
}
