// SPDX-License-Identifier: GPL-3.0-or-later
//
// Exercises the frame parser the agent uses on port 7532. It compiles anywhere
// a C++17 compiler exists, including the Windows PC where the SignalRGB plugin
// is developed, because stream_protocol.h deliberately depends on neither HID
// nor sockets.
//
// Build and run (one line):
//     g++ -std=c++17 -Wall -Wextra -Werror -I mac-agent
//     mac-agent/tests/stream_protocol_test.cpp -o stream_protocol_test
//     ./stream_protocol_test
//
// The cases mirror FrameStreamTests in tests/test_macstream.py, so the C++ and
// Python implementations of one wire format are held to one spec.

#include <cstdio>
#include <string>
#include <vector>

#include "stream_protocol.h"

namespace {

int failures = 0;
int checks = 0;

void check(bool condition, const std::string& what) {
    ++checks;
    if (!condition) {
        ++failures;
        std::printf("FAIL: %s\n", what.c_str());
    }
}

std::vector<unsigned char> frame(stream::Device device, unsigned char fill) {
    const std::size_t payload = stream::payload_size(device);
    std::vector<unsigned char> data{
        stream::kMagic0,
        stream::kMagic1,
        stream::kVersion,
        static_cast<unsigned char>(device),
        static_cast<unsigned char>(payload & 0xff),
        static_cast<unsigned char>((payload >> 8) & 0xff),
    };
    data.insert(data.end(), payload, fill);
    return data;
}

struct Captured {
    stream::Device device;
    std::vector<unsigned char> payload;
};

std::vector<Captured> drain(std::vector<unsigned char>& buffer) {
    std::vector<Captured> seen;
    stream::feed(buffer, [&seen](stream::FrameView view) {
        seen.push_back(Captured{
            view.device,
            std::vector<unsigned char>(view.payload, view.payload + view.length)});
    });
    return seen;
}

void test_sizes_match_the_spec() {
    check(stream::payload_size(stream::Device::K70) == 426, "K70 payload size");
    check(stream::payload_size(stream::Device::MM700) == 9, "MM700 payload size");
    check(stream::payload_size(stream::Device::G560) == 12, "G560 payload size");
    check(stream::payload_size(stream::Device::Scimitar) == 9, "Scimitar payload size");
    check(stream::kHeaderSize == 6, "header is 6 bytes");
}

void test_single_frame() {
    std::vector<unsigned char> buffer = frame(stream::Device::MM700, 0x7f);
    const auto seen = drain(buffer);
    check(seen.size() == 1, "one whole frame yields one frame");
    check(seen[0].device == stream::Device::MM700, "device id survives");
    check(seen[0].payload.size() == 9, "MM700 payload is 9 bytes");
    check(seen[0].payload[0] == 0x7f, "payload content survives");
    check(buffer.empty(), "nothing is retained after a whole frame");
}

void test_two_frames_in_one_read() {
    std::vector<unsigned char> buffer = frame(stream::Device::MM700, 1);
    const auto second = frame(stream::Device::K70, 2);
    buffer.insert(buffer.end(), second.begin(), second.end());
    const auto seen = drain(buffer);
    check(seen.size() == 2, "two frames in one read");
    check(seen[1].device == stream::Device::K70, "second frame is the K70");
    check(seen[1].payload.size() == 426, "K70 payload is 426 bytes");
    check(buffer.empty(), "buffer drains fully");
}

void test_byte_at_a_time() {
    const auto source = frame(stream::Device::K70, 0x40);
    std::vector<unsigned char> buffer;
    std::size_t total = 0;
    for (std::size_t index = 0; index < source.size(); ++index) {
        buffer.push_back(source[index]);
        total += drain(buffer).size();
    }
    check(total == 1, "a frame split byte by byte still parses exactly once");
}

void test_split_between_magic_bytes() {
    const auto source = frame(stream::Device::MM700, 3);
    std::vector<unsigned char> buffer{source.begin(), source.begin() + 1};
    check(drain(buffer).empty(), "a lone magic byte yields nothing yet");
    buffer.insert(buffer.end(), source.begin() + 1, source.end());
    check(drain(buffer).size() == 1, "the frame parses once the rest arrives");
}

void test_leading_garbage() {
    std::vector<unsigned char> buffer{0x00, 0xff, 0x12, 0x53, 0x11};
    const auto source = frame(stream::Device::G560, 5);
    buffer.insert(buffer.end(), source.begin(), source.end());
    const auto seen = drain(buffer);
    check(seen.size() == 1, "garbage in front of a frame is discarded");
    check(seen[0].device == stream::Device::G560, "the frame after garbage is intact");
}

void test_bad_header_recovers() {
    // Valid magic, bad version: the parser must drop one byte and resync.
    std::vector<unsigned char> buffer{
        stream::kMagic0, stream::kMagic1, 0x99, 0x00, 0x00, 0x00};
    const auto source = frame(stream::Device::MM700, 6);
    buffer.insert(buffer.end(), source.begin(), source.end());
    check(drain(buffer).size() == 1, "a bad header does not swallow the next frame");
}

void test_bad_length_recovers() {
    std::vector<unsigned char> buffer{
        stream::kMagic0, stream::kMagic1, stream::kVersion, 0x01, 0xff, 0xff};
    const auto source = frame(stream::Device::MM700, 7);
    buffer.insert(buffer.end(), source.begin(), source.end());
    check(drain(buffer).size() == 1, "a length that contradicts the device is rejected");
}

void test_unknown_device_recovers() {
    std::vector<unsigned char> buffer{
        stream::kMagic0, stream::kMagic1, stream::kVersion, 0x09, 0x09, 0x00};
    const auto source = frame(stream::Device::Scimitar, 8);
    buffer.insert(buffer.end(), source.begin(), source.end());
    check(drain(buffer).size() == 1, "an unknown device id is skipped");
}

void test_magic_inside_payload() {
    // A payload that happens to spell a header must not desync the stream.
    std::vector<unsigned char> buffer = frame(stream::Device::MM700, 0);
    buffer[6] = stream::kMagic0;
    buffer[7] = stream::kMagic1;
    buffer[8] = stream::kVersion;
    const auto second = frame(stream::Device::MM700, 0x21);
    buffer.insert(buffer.end(), second.begin(), second.end());
    const auto seen = drain(buffer);
    check(seen.size() == 2, "a magic inside a payload does not desync");
    check(seen[0].payload[0] == stream::kMagic0, "the payload is delivered verbatim");
}

void test_partial_magic_is_kept() {
    std::vector<unsigned char> buffer = frame(stream::Device::MM700, 9);
    buffer.push_back(stream::kMagic0);
    drain(buffer);
    check(buffer.size() == 1, "a trailing lone magic byte is kept");
    check(buffer[0] == stream::kMagic0, "and it is the right byte");
}

void test_buffer_is_bounded() {
    std::vector<unsigned char> buffer;
    for (int round = 0; round < 50; ++round) {
        buffer.insert(buffer.end(), 1024, 0x11);
        drain(buffer);
    }
    check(buffer.size() <= stream::kMaxBuffer, "a flood of garbage cannot grow the buffer");

    const auto source = frame(stream::Device::MM700, 0x33);
    buffer.insert(buffer.end(), source.begin(), source.end());
    check(drain(buffer).size() == 1, "the parser recovers after a flood");
}

void test_truncated_tail_is_kept() {
    std::vector<unsigned char> buffer = frame(stream::Device::MM700, 0x44);
    const auto partial = frame(stream::Device::K70, 0x55);
    buffer.insert(buffer.end(), partial.begin(), partial.begin() + 5);
    check(drain(buffer).size() == 1, "the whole frame is delivered");
    buffer.insert(buffer.end(), partial.begin() + 5, partial.end());
    check(drain(buffer).size() == 1, "the truncated frame completes on the next read");
}

void test_real_segmentation() {
    // The split the dump tool actually observed on the wire: one 432-byte read,
    // then the other three device frames spread across two reads.
    std::vector<unsigned char> whole;
    for (unsigned char id = 0; id < stream::kDeviceCount; ++id) {
        const auto one = frame(static_cast<stream::Device>(id), id);
        whole.insert(whole.end(), one.begin(), one.end());
    }
    const std::size_t cuts[] = {432, 465, whole.size()};
    std::vector<unsigned char> buffer;
    std::size_t start = 0;
    std::size_t seen = 0;
    for (std::size_t cut : cuts) {
        buffer.insert(
            buffer.end(),
            whole.begin() + static_cast<std::ptrdiff_t>(start),
            whole.begin() + static_cast<std::ptrdiff_t>(cut));
        seen += drain(buffer).size();
        start = cut;
    }
    check(seen == stream::kDeviceCount, "all four frames survive real segmentation");
    check(buffer.empty(), "nothing is left over");
}

}  // namespace

int main() {
    test_sizes_match_the_spec();
    test_single_frame();
    test_two_frames_in_one_read();
    test_byte_at_a_time();
    test_split_between_magic_bytes();
    test_leading_garbage();
    test_bad_header_recovers();
    test_bad_length_recovers();
    test_unknown_device_recovers();
    test_magic_inside_payload();
    test_partial_magic_is_kept();
    test_buffer_is_bounded();
    test_truncated_tail_is_kept();
    test_real_segmentation();

    std::printf("%d checks, %d failures\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
