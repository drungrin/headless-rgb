#!/bin/sh
# Compile and run the C++ checks for the Mac agent.
#
# On macOS this type-checks agent.cpp against the real hidapi and
# ApplicationServices headers. Elsewhere it falls back to the declaration-only
# shims in tests/shims, so the parser tests and the agent's type-check still run
# on the Windows PC where the SignalRGB plugin is developed.
#
# The shims declare the real signatures and nothing more: a wrong argument type
# or a call that does not exist still fails here. What they cannot catch is
# linking or any runtime behaviour, so install.sh on the Mac remains the final
# word.
#
#     sh mac-agent/tests/run.sh

set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
agent_dir=$(dirname "$script_dir")
build_dir="${TMPDIR:-/tmp}/headless-lights-agent-tests"

if [ -n "${CXX:-}" ]; then
    compiler="$CXX"
elif command -v clang++ >/dev/null 2>&1 && clang++ --version >/dev/null 2>&1; then
    compiler=clang++
elif [ -x /Library/Developer/CommandLineTools/usr/bin/clang++ ]; then
    # The default clang++ refuses to run until the Xcode licence is accepted;
    # the Command Line Tools one carries no such restriction. Same fallback
    # install.sh uses.
    compiler=/Library/Developer/CommandLineTools/usr/bin/clang++
elif command -v g++ >/dev/null 2>&1; then
    compiler=g++
else
    echo "no usable C++ compiler found; set CXX or install clang++/g++" >&2
    exit 1
fi

warnings="-std=c++17 -Wall -Wextra -Werror"

# The Command Line Tools clang cannot find its own headers unless the SDK is
# named, and xcrun fails when the Xcode licence has not been accepted. Fall back
# to the CLT SDK, which needs no licence.
sysroot=""
if [ "$(uname -s)" = "Darwin" ]; then
    if [ -n "${SDKROOT:-}" ]; then
        sysroot="$SDKROOT"
    elif detected=$(xcrun --show-sdk-path 2>/dev/null) && [ -n "$detected" ]; then
        sysroot="$detected"
    elif [ -d /Library/Developer/CommandLineTools/SDKs/MacOSX.sdk ]; then
        sysroot=/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk
    fi
    [ -n "$sysroot" ] && warnings="$warnings -isysroot $sysroot"
fi

mkdir -p "$build_dir"

# 1. The frame parser, which is pure and runs anywhere.
echo "== stream_protocol tests ($compiler)"
# shellcheck disable=SC2086
"$compiler" $warnings -I "$agent_dir" \
    "$script_dir/stream_protocol_test.cpp" \
    -o "$build_dir/stream_protocol_test"
"$build_dir/stream_protocol_test"

# 2. The agent itself. Only a type-check off a Mac: the shims have no bodies.
echo "== agent.cpp type-check"
if [ "$(uname -s)" = "Darwin" ]; then
    # A non-interactive ssh session does not load the shell profile, so brew is
    # often not on PATH. Same absolute path install.sh uses.
    if command -v brew >/dev/null 2>&1; then
        brew_bin=brew
    elif [ -x /opt/homebrew/bin/brew ]; then
        brew_bin=/opt/homebrew/bin/brew
    elif [ -x /usr/local/bin/brew ]; then
        brew_bin=/usr/local/bin/brew
    else
        echo "Homebrew not found; install hidapi with 'brew install hidapi'" >&2
        exit 1
    fi
    hidapi_prefix=$("$brew_bin" --prefix hidapi)
    # shellcheck disable=SC2086
    "$compiler" -fsyntax-only $warnings \
        -I "$hidapi_prefix/include" -I "$agent_dir" "$agent_dir/agent.cpp"
else
    # shellcheck disable=SC2086
    "$compiler" -fsyntax-only $warnings \
        -I "$agent_dir" -I "$script_dir/shims" \
        -include "$script_dir/shims/csignal_compat.h" \
        "$agent_dir/agent.cpp"
    echo "   (against shims; build on the Mac with install.sh to link for real)"
fi

echo "all agent checks passed"
