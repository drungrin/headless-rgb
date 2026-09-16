// Test-only ESM loader for SignalRGB's built-in modules.
//
// Node does not ship @SignalRGB/serial, but the production plugin must import it
// exactly the way SignalRGB requires. This maps the bare specifier onto a stub
// that delegates to a fake the harness installs on globalThis, so the plugin
// source under test stays byte-identical to the published file.

export async function resolve(specifier, context, nextResolve) {
	if (specifier === "@SignalRGB/serial") {
		return { url: "signalrgb-test:serial", shortCircuit: true };
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
	return nextLoad(url, context);
}
