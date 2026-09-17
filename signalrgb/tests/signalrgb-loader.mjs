// Test-only ESM loader for SignalRGB's built-in modules.
//
// Node does not ship @SignalRGB/serial, but the production plugin must import it
// exactly the way SignalRGB requires. This maps the bare specifier onto a stub
// that delegates to a fake the harness installs on globalThis, so the plugin
// source under test stays byte-identical to the published file.

// The Corsair fork imports two more of them. Neither is reached on the input
// path under test, so these only have to exist for the import to resolve.
const MODULES = {
	"@SignalRGB/serial": "signalrgb-test:serial",
	"@SignalRGB/Errors.js": "signalrgb-test:errors",
	"@SignalRGB/DeviceDiscovery": "signalrgb-test:discovery",
};

export async function resolve(specifier, context, nextResolve) {
	if (specifier in MODULES) {
		return { url: MODULES[specifier], shortCircuit: true };
	}
	return nextResolve(specifier, context);
}

export async function load(url, context, nextLoad) {
	if (url === "signalrgb-test:serial") {
		return {
			format: "module",
			shortCircuit: true,
			source: `
				const delegate = () => globalThis.__beelightSerial;
				export default {
					connect(options) { return delegate().connect(options); },
					disconnect() { return delegate().disconnect(); },
					isConnected() { return delegate().isConnected(); },
					write(data) { return delegate().write(data); },
					read(maxBytes, timeoutMs) { return delegate().read(maxBytes, timeoutMs); },
					getDeviceInfo() { return delegate().getDeviceInfo(); },
					availablePorts() { return delegate().availablePorts(); },
				};
			`,
		};
	}
	if (url === "signalrgb-test:errors") {
		return {
			format: "module",
			shortCircuit: true,
			source: `
				export function Assert(condition, message) {
					if (!condition) { throw new Error(message ?? "assertion failed"); }
				}
				export default { Assert };
			`,
		};
	}
	if (url === "signalrgb-test:discovery") {
		return {
			format: "module",
			shortCircuit: true,
			source: `
				export default {
					foundVirtualDevice() {},
					purgeDevice() {},
				};
			`,
		};
	}
	return nextLoad(url, context);
}
