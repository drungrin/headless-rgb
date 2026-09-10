// SPDX-License-Identifier: GPL-3.0-or-later
// G560 framing derives from OpenRGB. Corsair direct framing and the K70 MAX
// layout derive from OpenLinkHub. See mac-agent/README.md for attribution.

#include <arpa/inet.h>
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

    bool set_frame(const std::array<Color, 142>& frame) {
        if (!prepare()) {
            reset(false);
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
                return false;
            }
        }
        return true;
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
        return hid_read_timeout(device_, response.data(), response.size(), 500) >= 0;
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

    hid_device* device_ = nullptr;
    bool prepared_ = false;
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

    bool set_colors(const std::array<Color, 3>& colors) {
        if (!prepare()) {
            reset(false);
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
            return false;
        }
        return true;
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
        return hid_read_timeout(device_, response.data(), response.size(), 500) >= 0;
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

    hid_device* device_ = nullptr;
    bool prepared_ = false;
    std::chrono::steady_clock::time_point last_heartbeat_{};
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
        return device_ != nullptr;
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
        if (device_ == nullptr && !open()) {
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
                return false;
            }
        }
        return true;
    }

private:
    bool write_report(const std::array<unsigned char, 20>& report) {
        for (int attempt = 0; attempt < 3; ++attempt) {
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
            if (hid_write(device_, report.data(), report.size()) > 0) {
                std::array<unsigned char, 33> response{};
                hid_read_timeout(device_, response.data(), response.size(), 100);
                return true;
            }
        }
        return false;
    }

    hid_device* device_ = nullptr;
    std::array<bool, 4> prepared_{};
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
        if (device_ == nullptr && !open()) {
            return false;
        }
        constexpr std::array<unsigned char, 4> software{0x01, 0x03, 0x00, 0x02};
        constexpr std::array<unsigned char, 3> open_leds{0x0d, 0x00, 0x01};
        constexpr std::array<unsigned char, 2> write_color{0x06, 0x00};
        if (!prepared_) {
            if (!transfer(software.data(), software.size()) ||
                !transfer(open_leds.data(), open_leds.size())) {
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
        return transfer(
            write_color.data(),
            write_color.size(),
            payload.data(),
            payload.size());
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
        return transfer(heartbeat.data(), heartbeat.size());
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
        return hid_read_timeout(device_, response.data(), response.size(), 500) >= 0;
    }

    hid_device* device_ = nullptr;
    bool allow_software_;
    bool prepared_ = false;
    std::chrono::steady_clock::time_point last_heartbeat_{};
};

struct ApplyResult {
    bool k70;
    bool mm700;
    bool g560;
    bool scimitar;
};

enum class AgentEffect {
    Static,
    Watercolor,
    StrangerThings,
};

ApplyResult apply_color(
    K70Backend& k70,
    MM700Backend& mm700,
    G560Backend& g560,
    ScimitarBackend& scimitar,
    Color color) {
    return {
        k70.set_color(color),
        mm700.set_color(color),
        g560.set_color(color),
        scimitar.set_color(color),
    };
}

ApplyResult apply_watercolor(
    K70Backend& k70,
    MM700Backend& mm700,
    G560Backend& g560,
    ScimitarBackend& scimitar,
    double elapsed) {
    std::array<Color, 4> g560_colors{};
    for (std::size_t zone = 0; zone < g560_colors.size(); ++zone) {
        g560_colors[zone] = watercolor_color(0.28 + zone * 0.29, elapsed);
    }
    std::array<Color, 3> scimitar_colors{};
    for (std::size_t zone = 0; zone < scimitar_colors.size(); ++zone) {
        scimitar_colors[zone] = watercolor_color(0.49 + zone * 0.24, elapsed);
    }
    return {
        k70.set_watercolor(elapsed),
        mm700.set_watercolor(elapsed),
        g560.set_colors(g560_colors),
        scimitar.set_colors(scimitar_colors),
    };
}

ApplyResult apply_stranger(
    K70Backend& k70,
    MM700Backend& mm700,
    G560Backend& g560,
    ScimitarBackend& scimitar,
    double elapsed) {
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
        k70.set_stranger(elapsed),
        mm700.set_stranger(elapsed),
        g560.set_colors(g560_colors),
        scimitar.set_colors(scimitar_colors),
    };
}

const char* effect_name(AgentEffect effect) {
    switch (effect) {
        case AgentEffect::Watercolor:
            return "watercolor";
        case AgentEffect::StrangerThings:
            return "stranger-things";
        case AgentEffect::Static:
            return "static";
    }
    return "unknown";
}

ApplyResult apply_effect(
    AgentEffect effect,
    K70Backend& k70,
    MM700Backend& mm700,
    G560Backend& g560,
    ScimitarBackend& scimitar,
    double elapsed) {
    if (effect == AgentEffect::Watercolor) {
        return apply_watercolor(k70, mm700, g560, scimitar, elapsed);
    }
    return apply_stranger(k70, mm700, g560, scimitar, elapsed);
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

int create_server() {
    const int server = socket(AF_INET, SOCK_STREAM, 0);
    if (server < 0) {
        return -1;
    }
    int enabled = 1;
    setsockopt(server, SOL_SOCKET, SO_REUSEADDR, &enabled, sizeof(enabled));
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_port = htons(kListenPort);
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (bind(server, reinterpret_cast<sockaddr*>(&address), sizeof(address)) < 0 ||
        listen(server, 4) < 0) {
        close(server);
        return -1;
    }
    return server;
}

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
            } else {
                std::cerr << "invalid effect" << std::endl;
                return 64;
            }
        } else {
            std::cerr
                << "usage: mac-agent [--color RRGGBB | "
                   "--effect watercolor|stranger-things | "
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
    const auto effect_seconds = []() {
        return std::chrono::duration<double>(
            std::chrono::system_clock::now().time_since_epoch()).count();
    };
    ApplyResult last_result = effect == AgentEffect::Static
        ? apply_color(k70, mm700, g560, scimitar, current)
        : apply_effect(effect, k70, mm700, g560, scimitar, effect_seconds());
    std::cout << (effect == AgentEffect::Static
        ? result_line(current, last_result)
        : effect_result_line(effect_name(effect), last_result));

    const int server = create_server();
    if (server < 0) {
        std::cerr << "could not listen on 127.0.0.1:" << kListenPort << std::endl;
        return 3;
    }
    std::cout << "listening=127.0.0.1:" << kListenPort << std::endl;

    while (running.load()) {
        fd_set read_set;
        FD_ZERO(&read_set);
        FD_SET(server, &read_set);
        timeval timeout{0, 20000};
        const int ready = select(server + 1, &read_set, nullptr, nullptr, &timeout);
        if (ready > 0 && FD_ISSET(server, &read_set)) {
            const int client = accept(server, nullptr, nullptr);
            if (client >= 0) {
                timeval receive_timeout{2, 0};
                setsockopt(
                    client,
                    SOL_SOCKET,
                    SO_RCVTIMEO,
                    &receive_timeout,
                    sizeof(receive_timeout));
                std::array<char, 128> buffer{};
                const ssize_t count = recv(client, buffer.data(), buffer.size() - 1, 0);
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
                            last_result = apply_color(
                                k70,
                                mm700,
                                g560,
                                scimitar,
                                current);
                            response = result_line(current, last_result);
                        } else {
                            response = "ERROR color\n";
                        }
                    } else if (command == "EFFECT WATERCOLOR") {
                        effect = AgentEffect::Watercolor;
                        last_result = apply_effect(
                            effect,
                            k70,
                            mm700,
                            g560,
                            scimitar,
                            effect_seconds());
                        response = effect_result_line(effect_name(effect), last_result);
                    } else if (command == "EFFECT STRANGER-THINGS") {
                        effect = AgentEffect::StrangerThings;
                        last_result = apply_effect(
                            effect,
                            k70,
                            mm700,
                            g560,
                            scimitar,
                            effect_seconds());
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
        static auto next_effect_frame = std::chrono::steady_clock::now();
        const auto now = std::chrono::steady_clock::now();
        if (effect != AgentEffect::Static && now >= next_effect_frame) {
            last_result = apply_effect(
                effect,
                k70,
                mm700,
                g560,
                scimitar,
                effect_seconds());
            next_effect_frame = now + std::chrono::milliseconds(83);
        }
        if (!scimitar.heartbeat_if_due()) {
            std::cerr << "scimitar heartbeat failed" << std::endl;
        }
        if (!mm700.heartbeat_if_due()) {
            std::cerr << "mm700 heartbeat failed" << std::endl;
        }
        if (scimitar_mapping_ready && !scimitar_input.poll()) {
            std::cerr << "scimitar input listener failed" << std::endl;
            close(server);
            return 5;
        }
    }

    close(server);
    return running.load() ? 4 : 0;
}
