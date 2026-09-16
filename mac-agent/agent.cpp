// SPDX-License-Identifier: GPL-3.0-or-later
// G560 framing derives from OpenRGB. Corsair direct framing and the K70 MAX
// layout derive from OpenLinkHub. See mac-agent/README.md for attribution.

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <unistd.h>

#include <ApplicationServices/ApplicationServices.h>

#include <array>
#include <algorithm>
#include <atomic>
#include <cctype>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <sstream>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include <hidapi/hidapi.h>
#include <hidapi/hidapi_darwin.h>

#include "k70max_layout.h"
#include "stream_protocol.h"

namespace {

constexpr unsigned short kLogitechVendor = 0x046d;
constexpr unsigned short kG560Product = 0x0a78;
constexpr unsigned short kCorsairVendor = 0x1b1c;
constexpr unsigned short kK70MaxProduct = 0x1bc0;
constexpr unsigned short kMM700Product = 0x1b9b;
constexpr unsigned short kSlipstreamProduct = 0x2b00;
constexpr unsigned short kLightsyncUsagePage = 0xff43;
constexpr unsigned short kG560Usage = 0x0202;
constexpr int kScimitarControlInterface = 1;
constexpr unsigned char kScimitarEndpoint = 0x09;
constexpr int kListenPort = 7531;
constexpr int kStreamPort = 7532;

// A device that stops receiving frames for this long goes back to rendering the
// locally configured effect, so the Mac keeps its lighting when the PC sleeps,
// SignalRGB closes, or the SSH tunnel drops.
constexpr std::chrono::milliseconds kStreamTimeout{3000};

// Four devices need four connections; the headroom absorbs a reconnect that
// overlaps a half-open socket the peer has not closed yet.
constexpr std::size_t kMaxStreamClients = 6;
constexpr std::chrono::seconds kStreamIdleReap{120};

// Per-device floor between HID writes. The K70 costs four blocking round trips
// per frame. The G560 is deliberately slower: one frame is four synchronous
// request/reply transactions, and pushing those reports without waiting made
// macOS reject about 12.6% of them and eventually reset the complete USB device
// (including its audio interface).
constexpr std::array<std::chrono::milliseconds, stream::kDeviceCount> kStreamInterval{
    std::chrono::milliseconds{33},
    std::chrono::milliseconds{16},
    std::chrono::milliseconds{100},
    std::chrono::milliseconds{16},
};

// Bound on how much a single connection may hand us per loop pass, so one noisy
// peer cannot starve the HID writes or the Scimitar input poll.
constexpr std::size_t kStreamMaxDrainPerPass = 65536;

// Devices answer in well under a millisecond; this only bounds a wedged one.
constexpr int kHidReadTimeoutMs = 250;

constexpr std::chrono::seconds kBackendRetryDelay{2};

static_assert(
    stream::kLedCounts[0] == kK70LedCoordinates.size(),
    "the wire frame must carry one slot per K70 hardware channel");

std::atomic<bool> running{true};

struct Color {
    unsigned char red;
    unsigned char green;
    unsigned char blue;

    std::string hex() const {
        constexpr char digits[] = "0123456789abcdef";
        std::string result(6, '0');
        const unsigned char values[] = {red, green, blue};
        for (std::size_t index = 0; index < 3; ++index) {
            result[index * 2] = digits[values[index] >> 4];
            result[index * 2 + 1] = digits[values[index] & 0x0f];
        }
        return result;
    }
};

constexpr std::array<Color, 7> kWatercolorPalette{{
    {20, 222, 255},
    {65, 137, 255},
    {172, 91, 255},
    {255, 48, 194},
    {255, 153, 202},
    {255, 244, 151},
    {164, 255, 241},
}};

struct FlashStop {
    double phase;
    Color color;
    double opacity;
};

constexpr std::array<FlashStop, 8> kStrangerFlashStops{{
    {0.6270491803278688, {128, 43, 36}, 1.0},
    {0.6516393442622951, {128, 0, 0}, 0.0},
    {0.6536885245901639, {128, 0, 0}, 1.0},
    {0.6762295081967213, {128, 0, 0}, 0.27451},
    {0.7028688524590164, {128, 0, 0}, 0.290196},
    {0.7110655737704918, {128, 43, 36}, 1.0},
    {0.7356557377049180, {128, 0, 0}, 1.0},
    {0.7561475409836066, {128, 0, 0}, 0.0},
}};

int smooth_mix(int left, int right, double amount) {
    const double weight = (1.0 - std::cos(M_PI * amount)) / 2.0;
    return static_cast<int>(std::lround(left * (1.0 - weight) + right * weight));
}

Color watercolor_color(double position, double elapsed) {
    double phase = position * 0.92 + elapsed / 12.0;
    phase += 0.055 * std::sin(2.0 * M_PI * (position * 0.43 - elapsed / 17.0));
    phase += 0.018 * std::sin(2.0 * M_PI * (position * 1.71 + elapsed / 9.0));
    phase -= std::floor(phase);
    const double palette_position = phase * kWatercolorPalette.size();
    const std::size_t left_index =
        static_cast<std::size_t>(std::floor(palette_position));
    const double amount = palette_position - std::floor(palette_position);
    const Color left = kWatercolorPalette[left_index];
    const Color right =
        kWatercolorPalette[(left_index + 1) % kWatercolorPalette.size()];
    const double wash = 0.035 + 0.035 *
        (1.0 + std::sin(2.0 * M_PI * (position * 0.73 + elapsed / 13.0))) /
        2.0;
    const auto channel = [amount, wash](int left_value, int right_value) {
        const double mixed = smooth_mix(left_value, right_value, amount);
        return static_cast<unsigned char>(
            std::clamp(std::lround(mixed * (1.0 - wash) + 255.0 * wash),
                       0L,
                       255L));
    };
    return {
        channel(left.red, right.red),
        channel(left.green, right.green),
        channel(left.blue, right.blue),
    };
}

Color mix_color(Color left, Color right, double amount) {
    const auto channel = [amount](int left_value, int right_value) {
        return static_cast<unsigned char>(std::clamp(
            std::lround(left_value * (1.0 - amount) + right_value * amount),
            0L,
            255L));
    };
    return {
        channel(left.red, right.red),
        channel(left.green, right.green),
        channel(left.blue, right.blue),
    };
}

Color screen_color(Color base, Color layer, double opacity) {
    opacity = std::clamp(opacity, 0.0, 1.0);
    const auto channel = [opacity](int base_value, int layer_value) {
        return static_cast<unsigned char>(std::clamp(
            std::lround(
                255.0 - (255.0 - base_value) *
                (255.0 - layer_value * opacity) / 255.0),
            0L,
            255L));
    };
    return {
        channel(base.red, layer.red),
        channel(base.green, layer.green),
        channel(base.blue, layer.blue),
    };
}

double wrap_phase(double phase) {
    phase = std::fmod(phase, 1.0);
    return phase < 0.0 ? phase + 1.0 : phase;
}

double circular_peak(double phase, double center, double width) {
    double distance = std::abs(std::fmod(phase - center + 1.5, 1.0) - 0.5);
    if (distance >= width) {
        return 0.0;
    }
    const double normalized = 1.0 - distance / width;
    return normalized * normalized * (3.0 - 2.0 * normalized);
}

std::pair<Color, double> stranger_flash(double elapsed) {
    const double phase = std::fmod(elapsed, 7.0) / 7.0;
    if (phase < kStrangerFlashStops.front().phase ||
        phase > kStrangerFlashStops.back().phase) {
        return {{0, 0, 0}, 0.0};
    }
    for (std::size_t index = 0; index + 1 < kStrangerFlashStops.size(); ++index) {
        const FlashStop left = kStrangerFlashStops[index];
        const FlashStop right = kStrangerFlashStops[index + 1];
        if (left.phase <= phase && phase <= right.phase) {
            const double amount =
                (phase - left.phase) / (right.phase - left.phase);
            return {
                mix_color(left.color, right.color, amount),
                left.opacity * (1.0 - amount) + right.opacity * amount,
            };
        }
    }
    return {{0, 0, 0}, 0.0};
}

Color stranger_things_color(double position, double elapsed, int lane) {
    const double purple_amount = 0.5 + 0.5 * std::sin(
        2.0 * M_PI * (position * 0.73 + elapsed / 8.5));
    Color color = mix_color({6, 4, 23}, {10, 4, 46}, purple_amount);
    const double red_wave_fast = circular_peak(
        std::fmod(position - elapsed / 4.8 + 1000.0, 1.0),
        0.18 + (lane % 3) * 0.19,
        0.24);
    const double red_wave_slow = circular_peak(
        std::fmod(position - elapsed / 9.5 + 1000.0, 1.0),
        0.71 - (lane % 2) * 0.17,
        0.18);
    color = screen_color(color, {125, 0, 0}, red_wave_fast * 0.92);
    color = screen_color(color, {125, 0, 1}, red_wave_slow * 0.72);
    const double rain_phase_1 = std::fmod(
        position * 7.0 + elapsed * 0.82 + lane * 0.37,
        1.0);
    const double rain_phase_2 = std::fmod(
        position * 11.0 + elapsed * 0.57 + lane * 0.61,
        1.0);
    color = screen_color(
        color,
        {128, 0, 0},
        circular_peak(rain_phase_1, 0.08, 0.065) * 0.9);
    color = screen_color(
        color,
        {64, 0, 0},
        circular_peak(rain_phase_2, 0.56, 0.045) * 0.85);
    const auto flash = stranger_flash(elapsed);
    return screen_color(color, flash.first, flash.second);
}

Color borderlands4_color(double position, double elapsed, int lane) {
    const double red_pulse = 0.18 + 0.12 * (
        1.0 + std::sin(2.0 * M_PI * (elapsed / 3.0 + lane * 0.11))) / 2.0;
    Color color = mix_color({192, 0, 2}, {255, 18, 0}, red_pulse);
    const double orange_wave = circular_peak(
        wrap_phase(position - elapsed / 5.0 + lane * 0.071),
        0.36,
        0.25);
    const double gold_wave = circular_peak(
        wrap_phase(position - elapsed / 7.3 - lane * 0.043),
        0.74,
        0.17);
    color = screen_color(color, {255, 125, 0}, orange_wave * 0.92);
    return screen_color(color, {250, 180, 0}, gold_wave * 0.76);
}

bool parse_color(std::string value, Color& color) {
    if (!value.empty() && value.front() == '#') {
        value.erase(0, 1);
    }
    if (value.size() != 6) {
        return false;
    }
    for (const unsigned char character : value) {
        if (std::isxdigit(character) == 0) {
            return false;
        }
    }
    char* end = nullptr;
    const unsigned long rgb = std::strtoul(value.c_str(), &end, 16);
    if (end == nullptr || *end != '\0') {
        return false;
    }
    color = {
        static_cast<unsigned char>((rgb >> 16) & 0xff),
        static_cast<unsigned char>((rgb >> 8) & 0xff),
        static_cast<unsigned char>(rgb & 0xff),
    };
    return true;
}

class HidContext {
public:
    bool initialize() {
        initialized_ = hid_init() == 0;
        return initialized_;
    }

    ~HidContext() {
        if (initialized_) {
            hid_exit();
        }
    }

private:
    bool initialized_ = false;
};

void stop_handler(int) {
    running.store(false);
}

class K70Backend {
public:
    ~K70Backend() {
        reset(true);
    }

    bool set_color(Color color) {
        std::array<Color, kK70LedCoordinates.size()> frame{};
        frame.fill(color);
        return set_frame(frame);
    }

    bool set_watercolor(double elapsed) {
        std::array<Color, kK70LedCoordinates.size()> frame{};
        for (std::size_t index = 0; index < frame.size(); ++index) {
            const K70LedCoordinate coordinate = kK70LedCoordinates[index];
            if (coordinate.mapped) {
                frame[index] = watercolor_color(
                    0.12 + coordinate.x * 1.05 + coordinate.y * 0.18,
                    elapsed);
            }
        }
        return set_frame(frame);
    }

    bool set_stranger(double elapsed) {
        std::array<Color, kK70LedCoordinates.size()> frame{};
        for (std::size_t index = 0; index < frame.size(); ++index) {
            const K70LedCoordinate coordinate = kK70LedCoordinates[index];
            if (coordinate.mapped) {
                frame[index] = stranger_things_color(
                    0.11 + coordinate.x * 1.28 + coordinate.y * 0.2,
                    elapsed,
                    static_cast<int>(std::lround(coordinate.y * 5.0)));
            }
        }
        return set_frame(frame);
    }

    bool set_borderlands4(double elapsed) {
        std::array<Color, kK70LedCoordinates.size()> frame{};
        for (std::size_t index = 0; index < frame.size(); ++index) {
            const K70LedCoordinate coordinate = kK70LedCoordinates[index];
            if (coordinate.mapped) {
                frame[index] = borderlands4_color(
                    0.12 + coordinate.x * 1.16 + coordinate.y * 0.2,
                    elapsed,
                    static_cast<int>(std::lround(coordinate.y * 5.0)));
            }
        }
        return set_frame(frame);
    }

    bool set_frame(const std::array<Color, 142>& frame) {
        const auto now = std::chrono::steady_clock::now();
        if (now < next_retry_) {
            return false;
        }
        if (!prepare()) {
            reset(false);
            defer_retry();
            return false;
        }
        std::array<unsigned char, 428> color_data{};
        for (std::size_t channel = 0; channel < frame.size(); ++channel) {
            color_data[channel * 3] = frame[channel].red;
            color_data[channel * 3 + 1] = frame[channel].green;
            color_data[channel * 3 + 2] = frame[channel].blue;
        }
        std::array<unsigned char, 434> packet{};
        packet[0] = 0xac;
        packet[1] = 0x01;
        packet[4] = 0x12;
        std::memcpy(packet.data() + 6, color_data.data(), color_data.size());
        constexpr std::array<unsigned char, 2> first{0x06, 0x01};
        constexpr std::array<unsigned char, 2> next{0x07, 0x01};
        for (std::size_t offset = 0; offset < packet.size(); offset += 125) {
            const std::size_t size = std::min<std::size_t>(125, packet.size() - offset);
            const auto& endpoint = offset == 0 ? first : next;
            if (!transfer(
                    endpoint.data(),
                    endpoint.size(),
                    packet.data() + offset,
                    size)) {
                reset(false);
                defer_retry();
                return false;
            }
        }
        return true;
    }

private:
    bool open() {
        hid_device_info* devices = hid_enumerate(kCorsairVendor, kK70MaxProduct);
        std::string path;
        for (hid_device_info* info = devices; info != nullptr; info = info->next) {
            if (info->interface_number == 1) {
                path = info->path;
                break;
            }
        }
        hid_free_enumeration(devices);
        if (path.empty()) {
            return false;
        }
        device_ = hid_open_path(path.c_str());
        return device_ != nullptr;
    }

    bool prepare() {
        if (device_ == nullptr && !open()) {
            return false;
        }
        if (prepared_) {
            return true;
        }
        constexpr std::array<unsigned char, 4> software{0x01, 0x03, 0x00, 0x02};
        constexpr std::array<unsigned char, 3> activate_keys{0x0d, 0x01, 0x22};
        constexpr std::array<unsigned char, 3> activate_bar{0x0d, 0x00, 0x2e};
        prepared_ = transfer(software.data(), software.size()) &&
                    transfer(activate_keys.data(), activate_keys.size()) &&
                    transfer(activate_bar.data(), activate_bar.size());
        return prepared_;
    }

    bool transfer(
        const unsigned char* endpoint,
        std::size_t endpoint_size,
        const unsigned char* payload = nullptr,
        std::size_t payload_size = 0) {
        if (device_ == nullptr || 2 + endpoint_size + payload_size > 129) {
            return false;
        }
        std::array<unsigned char, 129> output{};
        output[1] = 0x08;
        std::memcpy(output.data() + 2, endpoint, endpoint_size);
        if (payload != nullptr && payload_size > 0) {
            std::memcpy(output.data() + 2 + endpoint_size, payload, payload_size);
        }
        if (hid_write(device_, output.data(), output.size()) < 0) {
            return false;
        }
        std::array<unsigned char, 128> response{};
        return hid_read_timeout(
                   device_, response.data(), response.size(), kHidReadTimeoutMs) > 0;
    }

    void reset(bool restore_hardware) {
        if (device_ == nullptr) {
            return;
        }
        if (restore_hardware && prepared_) {
            constexpr std::array<unsigned char, 4> hardware{0x01, 0x03, 0x00, 0x01};
            transfer(hardware.data(), hardware.size());
        }
        hid_close(device_);
        device_ = nullptr;
        prepared_ = false;
    }

    // Without this a present-but-wedged keyboard burns four blocking reads on
    // every single frame, which at streaming rates starves everything else.
    void defer_retry() {
        next_retry_ = std::chrono::steady_clock::now() + kBackendRetryDelay;
    }

    hid_device* device_ = nullptr;
    bool prepared_ = false;
    std::chrono::steady_clock::time_point next_retry_{};
};

class MM700Backend {
public:
    ~MM700Backend() {
        reset(true);
    }

    bool set_color(Color color) {
        return set_colors({color, color, color});
    }

    bool set_watercolor(double elapsed) {
        std::array<Color, 3> colors{};
        for (std::size_t zone = 0; zone < colors.size(); ++zone) {
            colors[zone] = watercolor_color(0.54 + zone * 0.34, elapsed);
        }
        return set_colors(colors);
    }

    bool set_stranger(double elapsed) {
        std::array<Color, 3> colors{};
        for (std::size_t zone = 0; zone < colors.size(); ++zone) {
            colors[zone] = stranger_things_color(
                0.57 + zone * 0.41,
                elapsed,
                8 + static_cast<int>(zone));
        }
        return set_colors(colors);
    }

    bool set_borderlands4(double elapsed) {
        std::array<Color, 3> colors{};
        for (std::size_t zone = 0; zone < colors.size(); ++zone) {
            colors[zone] = borderlands4_color(
                0.57 + zone * 0.41,
                elapsed,
                8 + static_cast<int>(zone));
        }
        return set_colors(colors);
    }

    bool heartbeat_if_due() {
        if (device_ == nullptr || !prepared_) {
            return true;
        }
        const auto now = std::chrono::steady_clock::now();
        if (now - last_heartbeat_ < std::chrono::seconds(20)) {
            return true;
        }
        constexpr std::array<unsigned char, 1> heartbeat{0x12};
        last_heartbeat_ = now;
        if (!transfer(heartbeat.data(), heartbeat.size())) {
            reset(false);
            return false;
        }
        return true;
    }

    bool set_colors(const std::array<Color, 3>& colors) {
        const auto now = std::chrono::steady_clock::now();
        if (now < next_retry_) {
            return false;
        }
        if (!prepare()) {
            reset(false);
            defer_retry();
            return false;
        }
        constexpr std::array<unsigned char, 2> write_color{0x06, 0x00};
        std::array<unsigned char, 20> payload{};
        payload[0] = 9;
        for (std::size_t zone = 0; zone < colors.size(); ++zone) {
            payload[4 + zone] = colors[zone].red;
            payload[7 + zone] = colors[zone].green;
            payload[10 + zone] = colors[zone].blue;
        }
        if (!transfer(
                write_color.data(),
                write_color.size(),
                payload.data(),
                payload.size())) {
            reset(false);
            defer_retry();
            return false;
        }
        // A colour write keeps the device awake, so it counts as a heartbeat.
        // Without this the 20s timer keeps firing an extra write+read into the
        // middle of a stream.
        last_heartbeat_ = now;
        return true;
    }

private:
    bool open() {
        hid_device_info* devices = hid_enumerate(kCorsairVendor, kMM700Product);
        std::string path;
        for (hid_device_info* info = devices; info != nullptr; info = info->next) {
            if (info->interface_number == 1) {
                path = info->path;
                break;
            }
        }
        hid_free_enumeration(devices);
        if (path.empty()) {
            return false;
        }
        device_ = hid_open_path(path.c_str());
        return device_ != nullptr;
    }

    bool prepare() {
        if (device_ == nullptr && !open()) {
            return false;
        }
        if (prepared_) {
            return true;
        }
        constexpr std::array<unsigned char, 4> software{0x01, 0x03, 0x00, 0x02};
        constexpr std::array<unsigned char, 3> activate{0x0d, 0x00, 0x01};
        prepared_ = transfer(software.data(), software.size()) &&
                    transfer(activate.data(), activate.size());
        last_heartbeat_ = std::chrono::steady_clock::now();
        return prepared_;
    }

    bool transfer(
        const unsigned char* endpoint,
        std::size_t endpoint_size,
        const unsigned char* payload = nullptr,
        std::size_t payload_size = 0) {
        if (device_ == nullptr || 2 + endpoint_size + payload_size > 65) {
            return false;
        }
        std::array<unsigned char, 65> output{};
        output[1] = 0x08;
        std::memcpy(output.data() + 2, endpoint, endpoint_size);
        if (payload != nullptr && payload_size > 0) {
            std::memcpy(output.data() + 2 + endpoint_size, payload, payload_size);
        }
        if (hid_write(device_, output.data(), output.size()) < 0) {
            return false;
        }
        std::array<unsigned char, 64> response{};
        return hid_read_timeout(
                   device_, response.data(), response.size(), kHidReadTimeoutMs) > 0;
    }

    void reset(bool restore_hardware) {
        if (device_ == nullptr) {
            return;
        }
        if (restore_hardware && prepared_) {
            constexpr std::array<unsigned char, 4> hardware{0x01, 0x03, 0x00, 0x01};
            transfer(hardware.data(), hardware.size());
        }
        hid_close(device_);
        device_ = nullptr;
        prepared_ = false;
    }

    void defer_retry() {
        next_retry_ = std::chrono::steady_clock::now() + kBackendRetryDelay;
    }

    hid_device* device_ = nullptr;
    bool prepared_ = false;
    std::chrono::steady_clock::time_point last_heartbeat_{};
    std::chrono::steady_clock::time_point next_retry_{};
};

class G560Backend {
public:
    bool open() {
        hid_device_info* devices =
            hid_enumerate(kLogitechVendor, kG560Product);
        std::string path;
        for (hid_device_info* info = devices; info != nullptr; info = info->next) {
            if (info->usage_page == kLightsyncUsagePage &&
                info->usage == kG560Usage) {
                path = info->path;
                break;
            }
        }
        hid_free_enumeration(devices);
        if (path.empty()) {
            return false;
        }
        device_ = hid_open_path(path.c_str());
        if (device_ == nullptr) {
            return false;
        }
        // The G560 protocol is request/reply. OpenRGB waits for the reply after
        // every report; making this descriptor non-blocking let the next zone go
        // out before the device had answered the previous one. A bounded read in
        // write_report() keeps the ordering without allowing a missing reply to
        // stall the whole agent.
        return true;
    }

    ~G560Backend() {
        if (device_ != nullptr) {
            hid_close(device_);
        }
    }

    bool set_color(Color color) {
        return set_colors({color, color, color, color});
    }

    bool set_colors(const std::array<Color, 4>& colors) {
        const auto now = std::chrono::steady_clock::now();
        if (now < next_retry_) {
            return false;
        }
        if (device_ == nullptr && !open()) {
            defer_retry();
            return false;
        }
        for (int zone = 0; zone < 4; ++zone) {
            if (!prepared_[static_cast<std::size_t>(zone)]) {
                std::array<unsigned char, 20> direct{};
                direct[0] = 0x11;
                direct[1] = 0xff;
                direct[2] = 0x04;
                direct[3] = 0xca;
                direct[4] = static_cast<unsigned char>(zone);
                if (!write_report(direct)) {
                    reset();
                    defer_retry();
                    return false;
                }
                prepared_[static_cast<std::size_t>(zone)] = true;
            }

            const Color color = colors[static_cast<std::size_t>(zone)];
            std::array<unsigned char, 20> report{};
            report[0] = 0x11;
            report[1] = 0xff;
            report[2] = 0x04;
            report[3] = 0x3a;
            report[4] = static_cast<unsigned char>(zone);
            report[5] = 0x01;
            report[6] = color.red;
            report[7] = color.green;
            report[8] = color.blue;
            report[9] = 0x02;
            if (!write_report(report)) {
                reset();
                defer_retry();
                return false;
            }
        }
        return true;
    }

private:
    bool write_report(const std::array<unsigned char, 20>& report) {
        constexpr int kReplyTimeoutMs = 20;
        for (int attempt = 0; attempt < 3; ++attempt) {
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
            if (hid_write(device_, report.data(), report.size()) <= 0) {
                continue;
            }

            // The reply payload is not useful, but the reply itself is the
            // device's flow control. Do not issue the next zone until it arrives.
            // OpenRGB uses a blocking hid_read here; 20ms keeps the same ordering
            // while bounding a missing reply to 60ms over all three attempts.
            std::array<unsigned char, 33> response{};
            if (hid_read_timeout(
                    device_, response.data(), response.size(), kReplyTimeoutMs) > 0) {
                return true;
            }
        }
        return false;
    }

    void reset() {
        if (device_ == nullptr) {
            return;
        }
        hid_close(device_);
        device_ = nullptr;
        prepared_.fill(false);
    }

    void defer_retry() {
        next_retry_ = std::chrono::steady_clock::now() + kBackendRetryDelay;
    }

    hid_device* device_ = nullptr;
    std::array<bool, 4> prepared_{};
    std::chrono::steady_clock::time_point next_retry_{};
};

class ScimitarInputMapper {
public:
    ~ScimitarInputMapper() {
        release_all();
        if (listener_ != nullptr) {
            hid_close(listener_);
        }
    }

    bool initialize() {
        if (!AXIsProcessTrusted()) {
            return false;
        }
        hid_device_info* devices =
            hid_enumerate(kCorsairVendor, kSlipstreamProduct);
        std::string path;
        for (hid_device_info* info = devices; info != nullptr; info = info->next) {
            if (info->interface_number == 2) {
                path = info->path;
                break;
            }
        }
        hid_free_enumeration(devices);
        if (path.empty()) {
            return false;
        }
        listener_ = hid_open_path(path.c_str());
        return listener_ != nullptr && hid_set_nonblocking(listener_, 1) == 0;
    }

    bool ready() const {
        return listener_ != nullptr;
    }

    bool poll() {
        if (listener_ == nullptr) {
            return false;
        }
        while (true) {
            std::array<unsigned char, 64> data{};
            const int count = hid_read(listener_, data.data(), data.size());
            if (count < 0) {
                release_all();
                hid_close(listener_);
                listener_ = nullptr;
                return false;
            }
            if (count == 0) {
                return true;
            }
            if (count < 6 || (data[0] != 1 && data[0] != 2) ||
                (data[1] != 0x02 && data[1] != 0x05 && data[1] != 0x09)) {
                continue;
            }
            const std::uint32_t report_mask =
                static_cast<std::uint32_t>(data[2]) |
                (static_cast<std::uint32_t>(data[3]) << 8) |
                (static_cast<std::uint32_t>(data[4]) << 16) |
                (static_cast<std::uint32_t>(data[5]) << 24);
            update(report_mask & kSideButtonMask);
        }
    }

private:
    static constexpr std::uint32_t kSideButtonMask = 0x0001ffe0;
    static constexpr std::array<CGKeyCode, 12> kKeyCodes{
        18, 19, 20, 21, 23, 22, 26, 28, 25, 29, 27, 24,
    };

    static void post(CGKeyCode key, bool pressed) {
        CGEventRef event = CGEventCreateKeyboardEvent(nullptr, key, pressed);
        if (event != nullptr) {
            CGEventPost(kCGHIDEventTap, event);
            CFRelease(event);
        }
    }

    void update(std::uint32_t next_mask) {
        const std::uint32_t changed = current_mask_ ^ next_mask;
        for (std::size_t index = 0; index < kKeyCodes.size(); ++index) {
            const std::uint32_t bit = static_cast<std::uint32_t>(1) << (index + 5);
            if ((changed & bit) != 0) {
                post(kKeyCodes[index], (next_mask & bit) != 0);
            }
        }
        current_mask_ = next_mask;
    }

    void release_all() {
        update(0);
    }

    hid_device* listener_ = nullptr;
    std::uint32_t current_mask_ = 0;
};

class ScimitarBackend {
public:
    explicit ScimitarBackend(bool allow_software)
        : allow_software_(allow_software) {}

    bool open() {
        hid_device_info* devices =
            hid_enumerate(kCorsairVendor, kSlipstreamProduct);
        std::string path;
        for (hid_device_info* info = devices; info != nullptr; info = info->next) {
            if (info->interface_number == kScimitarControlInterface) {
                path = info->path;
                break;
            }
        }
        hid_free_enumeration(devices);
        if (path.empty()) {
            return false;
        }
        device_ = hid_open_path(path.c_str());
        return device_ != nullptr;
    }

    ~ScimitarBackend() {
        if (device_ != nullptr) {
            if (prepared_) {
                constexpr std::array<unsigned char, 4> hardware{
                    0x01, 0x03, 0x00, 0x01};
                transfer(hardware.data(), hardware.size());
            }
            hid_close(device_);
        }
    }

    bool set_color(Color color) {
        return set_colors({color, color, color});
    }

    bool set_colors(const std::array<Color, 3>& colors) {
        if (!allow_software_) {
            return false;
        }
        if (std::chrono::steady_clock::now() < next_retry_) {
            return false;
        }
        if (device_ == nullptr && !open()) {
            defer_retry();
            return false;
        }
        constexpr std::array<unsigned char, 4> software{0x01, 0x03, 0x00, 0x02};
        constexpr std::array<unsigned char, 3> open_leds{0x0d, 0x00, 0x01};
        constexpr std::array<unsigned char, 2> write_color{0x06, 0x00};
        if (!prepared_) {
            if (!transfer(software.data(), software.size()) ||
                !transfer(open_leds.data(), open_leds.size())) {
                prepared_ = false;
                defer_retry();
                return false;
            }
            prepared_ = true;
        }
        std::array<unsigned char, 16> payload{};
        payload[0] = 12;
        for (std::size_t zone = 0; zone < colors.size(); ++zone) {
            payload[4 + zone] = colors[zone].red;
            payload[8 + zone] = colors[zone].green;
            payload[12 + zone] = colors[zone].blue;
        }
        last_heartbeat_ = std::chrono::steady_clock::now();
        const bool success = transfer(
            write_color.data(),
            write_color.size(),
            payload.data(),
            payload.size());
        if (!success) {
            prepared_ = false;
            defer_retry();
        }
        return success;
    }

    bool heartbeat_if_due() {
        if (device_ == nullptr) {
            return true;
        }
        const auto now = std::chrono::steady_clock::now();
        if (now - last_heartbeat_ < std::chrono::seconds(10)) {
            return true;
        }
        constexpr std::array<unsigned char, 1> heartbeat{0x12};
        last_heartbeat_ = now;
        const bool success = transfer(heartbeat.data(), heartbeat.size());
        if (!success) {
            prepared_ = false;
            defer_retry();
        }
        return success;
    }

private:
    bool transfer(
        const unsigned char* endpoint,
        std::size_t endpoint_size,
        const unsigned char* payload = nullptr,
        std::size_t payload_size = 0) {
        std::array<unsigned char, 65> output{};
        if (2 + endpoint_size + payload_size > output.size()) {
            return false;
        }
        output[1] = kScimitarEndpoint;
        std::memcpy(output.data() + 2, endpoint, endpoint_size);
        if (payload != nullptr && payload_size > 0) {
            std::memcpy(output.data() + 2 + endpoint_size, payload, payload_size);
        }
        if (hid_write(device_, output.data(), output.size()) < 0) {
            return false;
        }
        std::array<unsigned char, 64> response{};
        return hid_read_timeout(
                   device_, response.data(), response.size(), kHidReadTimeoutMs) > 0;
    }

    void defer_retry() {
        next_retry_ = std::chrono::steady_clock::now() + std::chrono::seconds(2);
    }

    hid_device* device_ = nullptr;
    bool allow_software_;
    bool prepared_ = false;
    std::chrono::steady_clock::time_point next_retry_{};
    std::chrono::steady_clock::time_point last_heartbeat_{};
};

struct ApplyResult {
    bool k70;
    bool mm700;
    bool g560;
    bool scimitar;
};

struct Backends {
    K70Backend& k70;
    MM700Backend& mm700;
    G560Backend& g560;
    ScimitarBackend& scimitar;
};

// Which devices the local renderer still owns. A device being streamed from the
// PC is masked out so the two sources never fight over the same hardware.
struct DeviceMask {
    bool k70;
    bool mm700;
    bool g560;
    bool scimitar;

    static DeviceMask all() {
        return {true, true, true, true};
    }
};

enum class AgentEffect {
    Static,
    Watercolor,
    StrangerThings,
    Borderlands4,
};

ApplyResult apply_color(
    Backends& backends,
    Color color,
    DeviceMask mask,
    ApplyResult previous) {
    return {
        mask.k70 ? backends.k70.set_color(color) : previous.k70,
        mask.mm700 ? backends.mm700.set_color(color) : previous.mm700,
        mask.g560 ? backends.g560.set_color(color) : previous.g560,
        mask.scimitar ? backends.scimitar.set_color(color) : previous.scimitar,
    };
}

ApplyResult apply_watercolor(
    Backends& backends,
    double elapsed,
    DeviceMask mask,
    ApplyResult previous) {
    std::array<Color, 4> g560_colors{};
    for (std::size_t zone = 0; zone < g560_colors.size(); ++zone) {
        g560_colors[zone] = watercolor_color(0.28 + zone * 0.29, elapsed);
    }
    std::array<Color, 3> scimitar_colors{};
    for (std::size_t zone = 0; zone < scimitar_colors.size(); ++zone) {
        scimitar_colors[zone] = watercolor_color(0.49 + zone * 0.24, elapsed);
    }
    return {
        mask.k70 ? backends.k70.set_watercolor(elapsed) : previous.k70,
        mask.mm700 ? backends.mm700.set_watercolor(elapsed) : previous.mm700,
        mask.g560 ? backends.g560.set_colors(g560_colors) : previous.g560,
        mask.scimitar ? backends.scimitar.set_colors(scimitar_colors)
                      : previous.scimitar,
    };
}

ApplyResult apply_stranger(
    Backends& backends,
    double elapsed,
    DeviceMask mask,
    ApplyResult previous) {
    std::array<Color, 4> g560_colors{};
    for (std::size_t zone = 0; zone < g560_colors.size(); ++zone) {
        g560_colors[zone] = stranger_things_color(
            0.31 + zone * 0.33,
            elapsed,
            20 + static_cast<int>(zone));
    }
    std::array<Color, 3> scimitar_colors{};
    for (std::size_t zone = 0; zone < scimitar_colors.size(); ++zone) {
        scimitar_colors[zone] = stranger_things_color(
            0.53 + zone * 0.27,
            elapsed,
            24 + static_cast<int>(zone));
    }
    return {
        mask.k70 ? backends.k70.set_stranger(elapsed) : previous.k70,
        mask.mm700 ? backends.mm700.set_stranger(elapsed) : previous.mm700,
        mask.g560 ? backends.g560.set_colors(g560_colors) : previous.g560,
        mask.scimitar ? backends.scimitar.set_colors(scimitar_colors)
                      : previous.scimitar,
    };
}

ApplyResult apply_borderlands4(
    Backends& backends,
    double elapsed,
    DeviceMask mask,
    ApplyResult previous) {
    std::array<Color, 4> g560_colors{};
    for (std::size_t zone = 0; zone < g560_colors.size(); ++zone) {
        g560_colors[zone] = borderlands4_color(
            0.31 + zone * 0.33,
            elapsed,
            20 + static_cast<int>(zone));
    }
    std::array<Color, 3> scimitar_colors{};
    for (std::size_t zone = 0; zone < scimitar_colors.size(); ++zone) {
        scimitar_colors[zone] = borderlands4_color(
            0.53 + zone * 0.27,
            elapsed,
            24 + static_cast<int>(zone));
    }
    return {
        mask.k70 ? backends.k70.set_borderlands4(elapsed) : previous.k70,
        mask.mm700 ? backends.mm700.set_borderlands4(elapsed) : previous.mm700,
        mask.g560 ? backends.g560.set_colors(g560_colors) : previous.g560,
        mask.scimitar ? backends.scimitar.set_colors(scimitar_colors)
                      : previous.scimitar,
    };
}

const char* effect_name(AgentEffect effect) {
    switch (effect) {
        case AgentEffect::Watercolor:
            return "watercolor";
        case AgentEffect::StrangerThings:
            return "stranger-things";
        case AgentEffect::Borderlands4:
            return "borderlands-4";
        case AgentEffect::Static:
            return "static";
    }
    return "unknown";
}

ApplyResult apply_effect(
    AgentEffect effect,
    Backends& backends,
    double elapsed,
    DeviceMask mask,
    ApplyResult previous) {
    if (effect == AgentEffect::Watercolor) {
        return apply_watercolor(backends, elapsed, mask, previous);
    }
    if (effect == AgentEffect::StrangerThings) {
        return apply_stranger(backends, elapsed, mask, previous);
    }
    return apply_borderlands4(backends, elapsed, mask, previous);
}

std::string result_line(Color color, ApplyResult result) {
    std::ostringstream output;
    output << (result.k70 && result.mm700 && result.g560 && result.scimitar
                   ? "OK"
                   : "PARTIAL")
           << " color=" << color.hex()
           << " k70=" << (result.k70 ? "ok" : "error")
           << " mm700=" << (result.mm700 ? "ok" : "error")
           << " g560=" << (result.g560 ? "ok" : "error")
           << " scimitar=" << (result.scimitar ? "ok" : "error") << "\n";
    return output.str();
}

std::string effect_result_line(const std::string& effect, ApplyResult result) {
    std::ostringstream output;
    output << (result.k70 && result.mm700 && result.g560 && result.scimitar
                   ? "OK"
                   : "PARTIAL")
           << " effect=" << effect
           << " k70=" << (result.k70 ? "ok" : "error")
           << " mm700=" << (result.mm700 ? "ok" : "error")
           << " g560=" << (result.g560 ? "ok" : "error")
           << " scimitar=" << (result.scimitar ? "ok" : "error") << "\n";
    return output.str();
}

int create_listener(int port, int backlog) {
    const int server = socket(AF_INET, SOCK_STREAM, 0);
    if (server < 0) {
        return -1;
    }
    int enabled = 1;
    setsockopt(server, SOL_SOCKET, SO_REUSEADDR, &enabled, sizeof(enabled));
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_port = htons(static_cast<uint16_t>(port));
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (bind(server, reinterpret_cast<sockaddr*>(&address), sizeof(address)) < 0 ||
        listen(server, backlog) < 0) {
        close(server);
        return -1;
    }
    // Accepting in a loop needs a non-blocking listener. On Darwin the accepted
    // socket does not inherit this flag, so the blocking recv on the control
    // port keeps working unchanged.
    const int flags = fcntl(server, F_GETFL, 0);
    if (flags >= 0) {
        fcntl(server, F_SETFL, flags | O_NONBLOCK);
    }
    return server;
}

// Receives per-LED frames from the SignalRGB bridge plugin and hands the newest
// one per device to the HID backends.
//
// Three properties matter here. Frames are coalesced into a single-slot mailbox
// per device, so a device that cannot keep up drops frames instead of building a
// backlog. Writes are paced per device, so a fast sender cannot push the K70
// past its four blocking round trips. And every device falls back to the local
// renderer once its frames stop arriving.
class StreamServer {
    // One frame per device, latest wins. Overwriting a still-pending frame is
    // the coalescing, so the backlog is one frame deep by construction.
    struct Target {
        std::array<Color, stream::kMaxLedCount> colors{};
        bool pending = false;
        bool streaming = false;
        std::chrono::steady_clock::time_point deadline{};
        std::chrono::steady_clock::time_point next_write{};
    };

    struct Connection {
        int fd = -1;
        std::vector<unsigned char> buffer;
        std::chrono::steady_clock::time_point last_activity{};
    };

public:
    ~StreamServer() {
        close_all();
    }

    bool start() {
        listener_ = create_listener(kStreamPort, static_cast<int>(kMaxStreamClients));
        return listener_ >= 0;
    }

    bool has_clients() const {
        return !connections_.empty();
    }

    void add_fds(fd_set& read_set, int& max_fd) const {
        if (listener_ < 0) {
            return;
        }
        FD_SET(listener_, &read_set);
        max_fd = std::max(max_fd, listener_);
        for (const Connection& connection : connections_) {
            FD_SET(connection.fd, &read_set);
            max_fd = std::max(max_fd, connection.fd);
        }
    }

    // Network only: never touches HID, so a burst of frames cannot delay the
    // device writes or the Scimitar input poll.
    void service(const fd_set& read_set, std::chrono::steady_clock::time_point now) {
        if (listener_ < 0) {
            return;
        }
        if (FD_ISSET(listener_, &read_set)) {
            accept_pending(now);
        }
        for (std::size_t index = connections_.size(); index-- > 0;) {
            Connection& connection = connections_[index];
            if (!FD_ISSET(connection.fd, &read_set)) {
                if (now - connection.last_activity > kStreamIdleReap) {
                    drop(index);
                }
                continue;
            }
            if (!drain(connection, now)) {
                drop(index);
            }
        }
    }

    DeviceMask local_mask() const {
        return {
            !targets_[0].streaming,
            !targets_[1].streaming,
            !targets_[2].streaming,
            !targets_[3].streaming,
        };
    }

    // Returns true when a device just fell back, so the caller knows to repaint
    // it with the local effect or colour.
    bool expire(std::chrono::steady_clock::time_point now) {
        bool released = false;
        for (Target& target : targets_) {
            if (target.streaming && now >= target.deadline) {
                target.streaming = false;
                target.pending = false;
                released = true;
            }
        }
        return released;
    }

    // A command on the control port takes the devices back immediately, so the
    // response line reflects writes that actually happened.
    void clear_all() {
        for (Target& target : targets_) {
            target.streaming = false;
            target.pending = false;
        }
    }

    // Writes at most one frame per device, pumping the caller's callback between
    // devices so a slow write cannot stall keyboard input.
    template <typename Pump>
    void flush(Backends& backends, Pump&& pump) {
        for (std::size_t index = 0; index < stream::kDeviceCount; ++index) {
            Target& target = targets_[index];
            if (!target.pending) {
                continue;
            }
            const auto now = std::chrono::steady_clock::now();
            if (now < target.next_write) {
                // Keep it pending: a newer frame will simply overwrite it.
                continue;
            }
            write_device(backends, static_cast<stream::Device>(index), target);
            target.pending = false;
            target.next_write = std::chrono::steady_clock::now() + kStreamInterval[index];
            pump();
        }
    }

    void close_all() {
        for (Connection& connection : connections_) {
            close(connection.fd);
        }
        connections_.clear();
        if (listener_ >= 0) {
            close(listener_);
            listener_ = -1;
        }
    }

private:
    void accept_pending(std::chrono::steady_clock::time_point now) {
        while (true) {
            const int client = accept(listener_, nullptr, nullptr);
            if (client < 0) {
                return;
            }
            if (connections_.size() >= kMaxStreamClients) {
                // Never leave the listener readable and unserviced, or select
                // spins at 100% CPU.
                close(client);
                continue;
            }
            const int flags = fcntl(client, F_GETFL, 0);
            if (flags >= 0) {
                fcntl(client, F_SETFL, flags | O_NONBLOCK);
            }
            Connection connection;
            connection.fd = client;
            connection.last_activity = now;
            connections_.push_back(std::move(connection));
        }
    }

    bool drain(Connection& connection, std::chrono::steady_clock::time_point now) {
        std::array<unsigned char, 4096> scratch{};
        std::size_t drained = 0;
        while (drained < kStreamMaxDrainPerPass) {
            const ssize_t count = recv(connection.fd, scratch.data(), scratch.size(), 0);
            if (count == 0) {
                return false;
            }
            if (count < 0) {
                // EINTR is retried on the next pass rather than here, so a
                // repeating signal cannot spin this loop.
                if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) {
                    break;
                }
                return false;
            }
            drained += static_cast<std::size_t>(count);
            connection.buffer.insert(
                connection.buffer.end(),
                scratch.begin(),
                scratch.begin() + static_cast<std::ptrdiff_t>(count));
            connection.last_activity = now;
            stream::feed(connection.buffer, [this, now](stream::FrameView frame) {
                store(frame, now);
            });
            if (static_cast<std::size_t>(count) < scratch.size()) {
                break;
            }
        }
        return true;
    }

    // Overwriting a still-pending frame is the coalescing: the backlog is one
    // frame deep by construction.
    void store(stream::FrameView frame, std::chrono::steady_clock::time_point now) {
        Target& target = targets_[static_cast<std::size_t>(frame.device)];
        const std::size_t leds = stream::led_count(frame.device);
        for (std::size_t led = 0; led < leds; ++led) {
            target.colors[led] = Color{
                frame.payload[led * 3],
                frame.payload[led * 3 + 1],
                frame.payload[led * 3 + 2],
            };
        }
        target.pending = true;
        target.streaming = true;
        target.deadline = now + kStreamTimeout;
    }

    void write_device(Backends& backends, stream::Device device, const Target& target) {
        switch (device) {
            case stream::Device::K70: {
                std::array<Color, kK70LedCoordinates.size()> frame{};
                std::copy(target.colors.begin(), target.colors.end(), frame.begin());
                backends.k70.set_frame(frame);
                return;
            }
            case stream::Device::MM700:
                backends.mm700.set_colors(
                    {target.colors[0], target.colors[1], target.colors[2]});
                return;
            case stream::Device::G560:
                backends.g560.set_colors(
                    {target.colors[0],
                     target.colors[1],
                     target.colors[2],
                     target.colors[3]});
                return;
            case stream::Device::Scimitar:
                backends.scimitar.set_colors(
                    {target.colors[0], target.colors[1], target.colors[2]});
                return;
        }
    }

    void drop(std::size_t index) {
        close(connections_[index].fd);
        connections_.erase(connections_.begin() + static_cast<std::ptrdiff_t>(index));
    }

    int listener_ = -1;
    std::vector<Connection> connections_;
    std::array<Target, stream::kDeviceCount> targets_{};
};

}  // namespace

int main(int argc, char** argv) {
    Color current{0, 0, 255};
    AgentEffect effect = AgentEffect::Static;
    bool request_accessibility = false;
    for (int index = 1; index < argc; ++index) {
        const std::string argument(argv[index]);
        if (argument == "--request-accessibility") {
            request_accessibility = true;
        } else if (argument == "--color" && index + 1 < argc) {
            if (!parse_color(argv[++index], current)) {
                std::cerr << "invalid color" << std::endl;
                return 64;
            }
            effect = AgentEffect::Static;
        } else if (argument == "--effect" && index + 1 < argc) {
            const std::string effect_argument(argv[++index]);
            if (effect_argument == "watercolor") {
                effect = AgentEffect::Watercolor;
            } else if (effect_argument == "stranger-things") {
                effect = AgentEffect::StrangerThings;
            } else if (effect_argument == "borderlands-4") {
                effect = AgentEffect::Borderlands4;
            } else {
                std::cerr << "invalid effect" << std::endl;
                return 64;
            }
        } else {
            std::cerr
                << "usage: mac-agent [--color RRGGBB | "
                   "--effect watercolor|stranger-things|borderlands-4 | "
                   "--request-accessibility]"
                << std::endl;
            return 64;
        }
    }

    if (request_accessibility) {
        const void* keys[] = {kAXTrustedCheckOptionPrompt};
        const void* values[] = {kCFBooleanTrue};
        CFDictionaryRef options = CFDictionaryCreate(
            kCFAllocatorDefault,
            keys,
            values,
            1,
            &kCFCopyStringDictionaryKeyCallBacks,
            &kCFTypeDictionaryValueCallBacks);
        const bool trusted = AXIsProcessTrustedWithOptions(options);
        CFRelease(options);
        std::cout << "accessibility=" << (trusted ? "trusted" : "not-trusted")
                  << std::endl;
        return trusted ? 0 : 77;
    }

    std::signal(SIGINT, stop_handler);
    std::signal(SIGTERM, stop_handler);
    std::signal(SIGPIPE, SIG_IGN);
    hid_darwin_set_open_exclusive(0);
    HidContext hid_context;
    if (!hid_context.initialize()) {
        std::cerr << "hid_init failed" << std::endl;
        return 1;
    }

    K70Backend k70;
    MM700Backend mm700;
    G560Backend g560;
    ScimitarInputMapper scimitar_input;
    const bool scimitar_mapping_ready = scimitar_input.initialize();
    std::cout << "scimitar-buttons="
              << (scimitar_mapping_ready ? "ready" : "unavailable")
              << std::endl;
    ScimitarBackend scimitar(scimitar_mapping_ready);
    Backends backends{k70, mm700, g560, scimitar};
    const auto effect_seconds = []() {
        return std::chrono::duration<double>(
            std::chrono::system_clock::now().time_since_epoch()).count();
    };
    ApplyResult last_result{};
    last_result = effect == AgentEffect::Static
        ? apply_color(backends, current, DeviceMask::all(), last_result)
        : apply_effect(
              effect, backends, effect_seconds(), DeviceMask::all(), last_result);
    std::cout << (effect == AgentEffect::Static
        ? result_line(current, last_result)
        : effect_result_line(effect_name(effect), last_result));

    const int server = create_listener(kListenPort, 4);
    if (server < 0) {
        std::cerr << "could not listen on 127.0.0.1:" << kListenPort << std::endl;
        return 3;
    }
    std::cout << "listening=127.0.0.1:" << kListenPort << std::endl;

    // A missing stream port must not be fatal: the plist restarts the agent on
    // exit, so a stale process holding 7532 would otherwise take the working
    // control port down with it in a restart loop.
    StreamServer stream;
    if (stream.start()) {
        std::cout << "streaming=127.0.0.1:" << kStreamPort << std::endl;
    } else {
        std::cerr << "could not listen on 127.0.0.1:" << kStreamPort
                  << "; SignalRGB streaming is unavailable" << std::endl;
    }

    const auto pump = [&]() {
        return !scimitar_mapping_ready || scimitar_input.poll();
    };
    auto next_effect_frame =
        std::chrono::steady_clock::now() + std::chrono::milliseconds(83);
    bool local_refresh_due = false;

    while (running.load()) {
        fd_set read_set;
        FD_ZERO(&read_set);
        FD_SET(server, &read_set);
        int max_fd = server;
        stream.add_fds(read_set, max_fd);
        // Poll faster while frames are arriving; idle behaviour is unchanged.
        timeval timeout{0, stream.has_clients() ? 5000 : 20000};
        const int ready = select(max_fd + 1, &read_set, nullptr, nullptr, &timeout);
        const auto loop_now = std::chrono::steady_clock::now();

        // Also runs on a bare timeout: select clears the set, so this pass only
        // reaps connections that have gone quiet.
        if (ready >= 0) {
            stream.service(read_set, loop_now);
        }
        // Expire before rendering so a device that just lapsed is relit in this
        // same pass instead of waiting for the next one.
        if (stream.expire(loop_now)) {
            local_refresh_due = true;
        }

        if (ready > 0 && FD_ISSET(server, &read_set)) {
            const int client = accept(server, nullptr, nullptr);
            if (client >= 0) {
                // Over an SSH tunnel the local end accepts immediately while the
                // command is still crossing the network, so the data routinely
                // arrives after accept() returns. A short receive timeout drops
                // those commands intermittently, which is why this waits up to
                // two seconds, matching the agent's long-standing behaviour.
                // The cost is unchanged from before: a client that connects and
                // never speaks stalls this loop for that window.
                std::array<char, 128> buffer{};
                ssize_t count = -1;
                fd_set command_set;
                FD_ZERO(&command_set);
                FD_SET(client, &command_set);
                timeval command_timeout{2, 0};
                if (select(client + 1, &command_set, nullptr, nullptr, &command_timeout) > 0) {
                    count = recv(client, buffer.data(), buffer.size() - 1, 0);
                }
                std::string response = "ERROR command\n";
                if (count > 0) {
                    std::string command(buffer.data(), static_cast<std::size_t>(count));
                    while (!command.empty() &&
                           (command.back() == '\n' || command.back() == '\r')) {
                        command.pop_back();
                    }
                    if (command.rfind("COLOR ", 0) == 0) {
                        Color requested{};
                        if (parse_color(command.substr(6), requested)) {
                            effect = AgentEffect::Static;
                            current = requested;
                            // An operator command wins over the stream, so every
                            // ok in the response is a write that really happened.
                            stream.clear_all();
                            last_result = apply_color(
                                backends,
                                current,
                                DeviceMask::all(),
                                last_result);
                            response = result_line(current, last_result);
                        } else {
                            response = "ERROR color\n";
                        }
                    } else if (command == "EFFECT WATERCOLOR") {
                        effect = AgentEffect::Watercolor;
                        stream.clear_all();
                        last_result = apply_effect(
                            effect,
                            backends,
                            effect_seconds(),
                            DeviceMask::all(),
                            last_result);
                        response = effect_result_line(effect_name(effect), last_result);
                    } else if (command == "EFFECT STRANGER-THINGS") {
                        effect = AgentEffect::StrangerThings;
                        stream.clear_all();
                        last_result = apply_effect(
                            effect,
                            backends,
                            effect_seconds(),
                            DeviceMask::all(),
                            last_result);
                        response = effect_result_line(effect_name(effect), last_result);
                    } else if (command == "EFFECT BORDERLANDS-4") {
                        effect = AgentEffect::Borderlands4;
                        stream.clear_all();
                        last_result = apply_effect(
                            effect,
                            backends,
                            effect_seconds(),
                            DeviceMask::all(),
                            last_result);
                        response = effect_result_line(effect_name(effect), last_result);
                    } else if (command == "STATUS") {
                        response = effect == AgentEffect::Static
                            ? result_line(current, last_result)
                            : effect_result_line(effect_name(effect), last_result);
                    }
                }
                send(client, response.data(), response.size(), 0);
                close(client);
            }
        }
        const auto now = std::chrono::steady_clock::now();
        if (now >= next_effect_frame) {
            const DeviceMask mask = stream.local_mask();
            if (effect != AgentEffect::Static) {
                last_result = apply_effect(
                    effect, backends, effect_seconds(), mask, last_result);
            } else if (local_refresh_due) {
                // Static mode never repaints on its own, so a device coming back
                // from streaming would otherwise stay frozen on SignalRGB's last
                // frame forever.
                last_result = apply_color(backends, current, mask, last_result);
            }
            local_refresh_due = false;
            next_effect_frame = now + std::chrono::milliseconds(83);
        }

        stream.flush(backends, pump);

        if (!scimitar.heartbeat_if_due()) {
            std::cerr << "scimitar heartbeat failed" << std::endl;
        }
        if (!mm700.heartbeat_if_due()) {
            std::cerr << "mm700 heartbeat failed" << std::endl;
        }
        if (!pump()) {
            std::cerr << "scimitar input listener failed" << std::endl;
            stream.close_all();
            close(server);
            return 5;
        }
    }

    stream.close_all();
    close(server);
    return running.load() ? 4 : 0;
}
