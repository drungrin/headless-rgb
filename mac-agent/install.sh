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

/usr/bin/clang++ \
    -std=c++17 -Wall -Wextra -Werror \
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
