#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later

set -eu

if [ "$#" -ne 0 ]; then
    echo "usage: $0" >&2
    exit 64
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
hidapi_prefix=$(/opt/homebrew/bin/brew --prefix hidapi)
install_root="$HOME/Library/Application Support/headless-lights"
binary_dir="$install_root/bin"
launch_agent_dir="$HOME/Library/LaunchAgents"
launch_agent="$launch_agent_dir/com.headless-lights.agent.plist"
launch_domain="gui/$(id -u)"

/bin/mkdir -p "$binary_dir" "$launch_agent_dir"

# /usr/bin/clang++ refuses to run until the Xcode licence has been accepted,
# which needs an interactive sudo. The Command Line Tools compiler carries no
# such restriction, so fall back to it when the default one is blocked. It needs
# -isysroot to find its own headers.
compiler=/usr/bin/clang++
sysroot=""
if ! "$compiler" --version >/dev/null 2>&1; then
    clt=/Library/Developer/CommandLineTools
    if [ -x "$clt/usr/bin/clang++" ]; then
        compiler="$clt/usr/bin/clang++"
        sysroot="$clt/SDKs/MacOSX.sdk"
        echo "note: using the Command Line Tools compiler (Xcode licence not accepted)"
    else
        echo "no usable clang++; run 'sudo xcodebuild -license' or install the Command Line Tools" >&2
        exit 1
    fi
fi

"$compiler" \
    -std=c++17 -Wall -Wextra -Werror \
    ${sysroot:+-isysroot "$sysroot"} \
    -I "$hidapi_prefix/include" \
    -I "$script_dir" \
    "$script_dir/agent.cpp" \
    -L "$hidapi_prefix/lib" -lhidapi \
    -framework ApplicationServices \
    -Wl,-rpath,"$hidapi_prefix/lib" \
    -o "$binary_dir/headless-lights-agent.new"
/usr/bin/codesign --force --sign - \
    --identifier com.headless-lights.agent \
    "$binary_dir/headless-lights-agent.new"
/bin/mv -f \
    "$binary_dir/headless-lights-agent.new" \
    "$binary_dir/headless-lights-agent"

/usr/bin/sed "s|__HOME__|$HOME|g" \
    "$script_dir/com.headless-lights.agent.plist.in" > "$launch_agent.new"
/usr/bin/plutil -lint "$launch_agent.new"
/bin/mv -f "$launch_agent.new" "$launch_agent"

/bin/launchctl bootout "$launch_domain" "$launch_agent" 2>/dev/null || true
/bin/launchctl bootstrap "$launch_domain" "$launch_agent"
/bin/launchctl kickstart -k "$launch_domain/com.headless-lights.agent"

echo "installed: $binary_dir/headless-lights-agent"
echo "loaded: $launch_domain/com.headless-lights.agent"

# The ad-hoc signature means TCC keys on the binary's CDHash, so every rebuild
# invalidates the Accessibility grant even though the entry still shows in the
# list. Say so here rather than letting it surface later as scimitar=error.
#
# Over ssh the question cannot be asked here at all: TCC answers for the
# session's responsible process, so the ssh session's own grant reports every
# binary it starts as trusted, including one TCC has never seen. Only the
# launchd agent can answer for itself, and it does, in its log.
if [ -n "${SSH_CONNECTION:-}" ]; then
    echo
    echo "accessibility: not checked (over ssh this check answers for the ssh"
    echo "session, not for this build). The agent settles it:"
    echo "  tail -n 4 \"$install_root/agent.log\""
    echo "'scimitar-buttons=ready' means granted; 'reason=accessibility' means"
    echo "this build still needs it. Every rebuild does: in System Settings >"
    echo "Privacy & Security > Accessibility, remove any existing"
    echo "'headless-lights-agent' entry and add this path again:"
    echo "  $binary_dir/headless-lights-agent"
    echo "Then restart the agent. Granting it is not enough on its own: a"
    echo "running process keeps the answer TCC gave it when it launched."
    echo "  launchctl kickstart -k $launch_domain/com.headless-lights.agent"
elif ! "$binary_dir/headless-lights-agent" --request-accessibility >/dev/null 2>&1; then
    echo
    echo "Accessibility: NOT granted for this build."
    echo "The Scimitar needs it for RGB and for its 12 side buttons."
    echo "In System Settings > Privacy & Security > Accessibility, remove any"
    echo "existing 'headless-lights-agent' entry and add this path again:"
    echo "  $binary_dir/headless-lights-agent"
    echo "Then restart the agent. Granting it is not enough on its own: a"
    echo "running process keeps the answer TCC gave it when it launched."
    echo "  launchctl kickstart -k $launch_domain/com.headless-lights.agent"
else
    echo "accessibility: granted"
fi
