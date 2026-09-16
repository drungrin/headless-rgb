// SPDX-License-Identifier: GPL-3.0-or-later
// Frame protocol spoken on 127.0.0.1:7532 by the SignalRGB bridge plugin.
//
// Pure parsing: no HID, no sockets, no dependency on agent.cpp. See
// mac-agent/README.md for the wire specification.

#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <vector>

namespace stream {

constexpr unsigned char kMagic0 = 0x53;  // 'S'
constexpr unsigned char kMagic1 = 0x47;  // 'G'
constexpr unsigned char kVersion = 0x01;
constexpr std::size_t kHeaderSize = 6;

enum class Device : unsigned char {
    K70 = 0,
    MM700 = 1,
    G560 = 2,
    Scimitar = 3,
};

constexpr std::size_t kDeviceCount = 4;

// K70 frames carry every hardware channel, including the 26 that have no
// physical LED, so the agent never needs its own mapping table.
constexpr std::array<std::size_t, kDeviceCount> kLedCounts{142, 3, 4, 3};

constexpr std::size_t kMaxLedCount = 142;
static_assert(kLedCounts[0] == kMaxLedCount, "K70 frame size must match kMaxLedCount");

constexpr std::size_t led_count(Device device) {
    return kLedCounts[static_cast<std::size_t>(device)];
}

constexpr std::size_t payload_size(Device device) {
    return led_count(device) * 3;
}

constexpr std::size_t kMaxFrameSize = kHeaderSize + kMaxLedCount * 3;

// Accumulator ceiling. Two whole K70 frames is ample for a sender that only
// ever has one frame in flight, and it bounds memory if a peer floods us.
constexpr std::size_t kMaxBuffer = kMaxFrameSize * 2;

struct FrameView {
    Device device;
    const unsigned char* payload;
    std::size_t length;
};

inline bool valid_header(const unsigned char* header) {
    if (header[0] != kMagic0 || header[1] != kMagic1 || header[2] != kVersion) {
        return false;
    }
    if (header[3] >= kDeviceCount) {
        return false;
    }
    const std::size_t length =
        static_cast<std::size_t>(header[4]) |
        (static_cast<std::size_t>(header[5]) << 8);
    return length == payload_size(static_cast<Device>(header[3]));
}

// Consumes every complete frame in `buffer`, calling `sink` with a view of each
// one, and leaves the unconsumed tail in place. The view is only valid until
// `sink` returns.
//
// Resync mirrors FrameStream.feed in src/headless_lights/beelight/protocol.py:
// scan for the magic, drop the garbage in front of it, stop on a short buffer,
// and drop exactly one byte on a bad header so a hostile stream cannot loop.
template <typename Sink>
void feed(std::vector<unsigned char>& buffer, Sink&& sink) {
    std::size_t cursor = 0;
    while (true) {
        std::size_t start = cursor;
        while (start + 1 < buffer.size() &&
               !(buffer[start] == kMagic0 && buffer[start + 1] == kMagic1)) {
            ++start;
        }
        if (start + 1 >= buffer.size()) {
            // Keep a trailing lone byte: it may be the first half of a magic.
            // Never move the cursor back over bytes already consumed.
            if (!buffer.empty() && buffer.size() - 1 > cursor) {
                cursor = buffer.size() - 1;
            }
            break;
        }
        cursor = start;
        if (buffer.size() - cursor < kHeaderSize) {
            break;
        }
        const unsigned char* header = buffer.data() + cursor;
        if (!valid_header(header)) {
            ++cursor;
            continue;
        }
        const std::size_t length =
            static_cast<std::size_t>(header[4]) |
            (static_cast<std::size_t>(header[5]) << 8);
        if (buffer.size() - cursor < kHeaderSize + length) {
            break;
        }
        sink(FrameView{
            static_cast<Device>(header[3]),
            header + kHeaderSize,
            length,
        });
        cursor += kHeaderSize + length;
    }

    if (cursor > 0) {
        buffer.erase(buffer.begin(), buffer.begin() + static_cast<std::ptrdiff_t>(cursor));
    }
    if (buffer.size() > kMaxBuffer) {
        // Nothing parseable and the peer keeps talking: drop the oldest bytes
        // rather than growing without bound.
        buffer.erase(
            buffer.begin(),
            buffer.begin() + static_cast<std::ptrdiff_t>(buffer.size() - kMaxBuffer));
    }
}

}  // namespace stream
