// Drive signalrgb/beelight.js under Node and print, as JSON, every byte it puts
// on the serial port. tests/test_beelight_plugin_frames.py decodes those bytes
// with this project's own Beelight parser.
//
// Scripted device responses arrive on stdin as JSON, encoded by the Python
// implementation, so the handshake is exercised against real frames.
//
//   node --experimental-loader ./signalrgb/tests/signalrgb-loader.mjs \
//        signalrgb/tests/dump_beelight_frames.mjs < responses.json

import { readFileSync } from "node:fs";
import {
	busyWait,
	bytesToHex,
	hexToBytes,
	installGlobals,
	makeSerial,
} from "./beelight_harness.mjs";
import * as plugin from "../beelight.js";

const scripted = JSON.parse(readFileSync(0, "utf8"));
const { ackFirmware, ackSyncConfig, ackControl, heartbeatRequest, decoy } = scripted;

// Matches _canvas() in tests/test_beelight_plugin_frames.py.
function canvas(x, y) {
	return [(x * 11 + 3) & 0xff, (y * 29 + 7) & 0xff, (x * 7 + y * 13 + 1) & 0xff];
}

// Writes 1..5 are FIRMWARE, SYNC_CONFIG, WORK_MODE, SWITCH and BRIGHTNESS.
function handshakeResponses(extra = {}) {
	const responses = {
		1: [ackFirmware],
		2: [ackSyncConfig],
		3: [ackControl],
		4: [ackControl],
		5: [ackControl],
	};
	for (const [index, frames] of Object.entries(extra)) {
		responses[index] = (responses[index] || []).concat(frames);
	}
	return responses;
}

function start(options = {}) {
	const serial = makeSerial({
		responses: options.responses === undefined ? handshakeResponses() : options.responses,
		failConnect: options.failConnect || false,
	});
	const { logs } = installGlobals({
		serial,
		canvas,
		lightingMode: options.lightingMode,
		forcedColor: options.forcedColor,
		shutdownColor: options.shutdownColor,
	});
	const initialized = plugin.Initialize();
	return { serial, logs, initialized };
}

function snapshot(session, extra = {}) {
	return Object.assign(
		{
			initialized: session.initialized,
			writes: session.serial.writes.slice(),
			logs: session.logs.slice(),
			ledCount: globalThis.device.ledNames === null
				? 0
				: globalThis.device.ledNames.length,
			size: globalThis.device.size,
			frameRateTargets: globalThis.device.frameRateTargets.slice(),
			connectOptions: session.serial.connectOptions,
			isOpen: session.serial.isOpen,
		},
		extra,
	);
}

const output = {};

output.metadata = {
	name: plugin.Name(),
	type: plugin.Type(),
	deviceType: plugin.DeviceType(),
	vendorId: plugin.VendorId(),
	productId: plugin.ProductId(),
	size: plugin.Size(),
	// A single-function CDC device has no interface to select, so the plugin
	// must not export Validate(); SignalRGB would never match the port.
	hasValidate: typeof plugin.Validate === "function",
	parameters: plugin.ControllableParameters().map((entry) => entry.property),
};

// --- handshake -------------------------------------------------------------
{
	const session = start();
	output.handshake = snapshot(session);
	plugin.Shutdown();
}

// --- one rendered frame ----------------------------------------------------
{
	const session = start();
	const before = session.serial.writes.length;
	plugin.Render();
	output.render = snapshot(session, { handshakeWrites: before });
	plugin.Shutdown();
}

// --- pacing: a second Render in the same interval must not write -----------
{
	const session = start();
	const before = session.serial.writes.length;
	plugin.Render();
	const afterFirst = session.serial.writes.length;
	plugin.Render();
	const afterSecond = session.serial.writes.length;
	busyWait(40);
	plugin.Render();
	output.pacing = snapshot(session, {
		handshakeWrites: before,
		afterFirst,
		afterSecond,
		afterInterval: session.serial.writes.length,
	});
	plugin.Shutdown();
}

// --- forced colour ---------------------------------------------------------
{
	const session = start({ lightingMode: "Forced", forcedColor: "#ff6600" });
	const before = session.serial.writes.length;
	plugin.Render();
	output.forced = snapshot(session, { handshakeWrites: before });
	plugin.Shutdown();
}

// --- an unsolicited heartbeat must be answered -----------------------------
{
	// Queued behind the first pixel frame, so it is read by the next Render.
	const session = start({ responses: handshakeResponses({ 6: [heartbeatRequest] }) });
	const before = session.serial.writes.length;
	plugin.Render();
	busyWait(40);
	plugin.Render();
	output.heartbeat = snapshot(session, { handshakeWrites: before });
	plugin.Shutdown();
}

// --- a decoy frame must be ignored, not answered ---------------------------
{
	const session = start({ responses: handshakeResponses({ 6: [decoy] }) });
	const before = session.serial.writes.length;
	plugin.Render();
	busyWait(40);
	plugin.Render();
	output.decoy = snapshot(session, { handshakeWrites: before });
	plugin.Shutdown();
}

// --- shutdown --------------------------------------------------------------
{
	const session = start({ shutdownColor: "#123456" });
	const before = session.serial.writes.length;
	plugin.Render();
	const beforeShutdown = session.serial.writes.length;
	plugin.Shutdown();
	output.shutdown = snapshot(session, {
		handshakeWrites: before,
		beforeShutdown,
	});
}

// --- an unresponsive device ------------------------------------------------
{
	const session = start({ responses: {} });
	output.silentDevice = snapshot(session);
	plugin.Shutdown();
}

// --- the port refuses to open ----------------------------------------------
{
	const session = start({ failConnect: true });
	// Render must stay harmless while disconnected.
	plugin.Render();
	output.connectFailure = snapshot(session);
	plugin.Shutdown();
}

// --- the port disappears after a good handshake ----------------------------
{
	const session = start();
	plugin.Render();
	const beforeUnplug = session.serial.writes.length;
	session.disconnectedExternally = session.serial.disconnect();
	busyWait(40);
	plugin.Render();
	output.unplugged = snapshot(session, { beforeUnplug });
	plugin.Shutdown();
}

// --- protocol vectors, decoded and encoded straight from the port ----------
{
	const { encodeFrame, decodeFrame, controlData, parseSyncConfig, FrameStream } =
		plugin.__testProtocol;

	const vendorKey = hexToBytes("a64361aada41e7");
	const stream = new FrameStream();
	const firstHalf = hexToBytes("ff" + "55aa5a0900ff3441");
	const secondHalf = hexToBytes("af2ef8c641ae" + "55aa5a");

	output.vectors = {
		decodedFirmware: decodeFrame(hexToBytes("55aa5a0900ff3441af2ef8c641ae")),
		encodedControl: bytesToHex(
			encodeFrame(5, [0, 0, 0, 0], { key: vendorKey }),
		),
		controlData: {
			switchOn: bytesToHex(controlData(1, [1])),
			brightness: bytesToHex(controlData(2, [100, 0])),
			color: bytesToHex(controlData(4, [255, 0, 0])),
		},
		syncConfig: parseSyncConfig(
			hexToBytes("21000221000000000000000000000000000000"),
		),
		syncConfigTruncated: parseSyncConfig([0x21]),
		syncConfigZero: parseSyncConfig([0x00, 0x00, 0x02]),
		syncConfigHuge: parseSyncConfig([0xff, 0xff, 0x02]),
		decodedGarbage: decodeFrame(hexToBytes("55aa5a0300010203")),
		// Split across two reads, with a junk byte in front and a partial
		// header trailing: the stream must still yield exactly one frame.
		splitStream: [
			stream.feed(firstHalf).map(bytesToHex),
			stream.feed(secondHalf).map(bytesToHex),
		],
	};
}

process.stdout.write(JSON.stringify(output));
