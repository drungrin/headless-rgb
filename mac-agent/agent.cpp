// SPDX-License-Identifier: GPL-3.0-or-later
// G560 framing derives from OpenRGB; Scimitar framing derives from OpenLinkHub.

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <unistd.h>

#include <array>
#include <algorithm>
#include <atomic>
#include <cctype>
#include <chrono>
#include <cmath>
#include <csignal>
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
#include <iCUESDK.h>

namespace {

constexpr unsigned short kLogitechVendor = 0x046d;
constexpr unsigned short kG560Product = 0x0a78;
constexpr unsigned short kCorsairVendor = 0x1b1c;
constexpr unsigned short kSlipstreamProduct = 0x2b00;
constexpr unsigned short kLightsyncUsagePage = 0xff43;
constexpr unsigned short kG560Usage = 0x0202;
constexpr int kScimitarControlInterface = 1;
constexpr unsigned char kScimitarEndpoint = 0x09;
constexpr int kListenPort = 7531;

std::atomic<bool> running{true};
std::atomic<int> icue_session_state{CSS_Closed};

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

void icue_state_changed(void*, const CorsairSessionStateChanged* event) {
    if (event == nullptr) {
        return;
    }
    icue_session_state.store(event->state);
    std::cout << "icue-state=" << event->state
              << " server=" << event->details.serverVersion.major << "."
              << event->details.serverVersion.minor << "."
              << event->details.serverVersion.patch
              << " host=" << event->details.serverHostVersion.major << "."
              << event->details.serverHostVersion.minor << "."
              << event->details.serverHostVersion.patch << std::endl;
}

class ICueBackend {
public:
    explicit ICueBackend(bool include_mousemat)
        : include_mousemat_(include_mousemat) {}

    bool connect() {
        if (CorsairConnect(icue_state_changed, nullptr) != CE_Success) {
            return false;
        }
        const auto deadline = std::chrono::steady_clock::now() +
                              std::chrono::seconds(15);
        while (icue_session_state.load() != CSS_Connected &&
               std::chrono::steady_clock::now() < deadline) {
            const int state = icue_session_state.load();
            if (state == CSS_ConnectionRefused || state == CSS_ConnectionLost) {
                return false;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }
        if (icue_session_state.load() != CSS_Connected) {
            return false;
        }
        CorsairSetLayerPriority(255);
        return enumerate_devices();
    }

    ~ICueBackend() {
        CorsairDisconnect();
    }

    bool connected() const {
        return icue_session_state.load() == CSS_Connected;
    }

    bool set_color(Color color) {
        if (!connected() && !connect()) {
            return false;
        }
        bool success = true;
        for (const Device& device : devices_) {
            std::vector<Color> frame(device.leds.size(), color);
            success = write_frame(device, frame, true) && success;
        }
        return success;
    }

    bool set_watercolor(double elapsed) {
        bool success = true;
        for (const Device& device : devices_) {
            if (device.leds.empty()) {
                success = false;
                continue;
            }
            std::vector<Color> frame;
            frame.reserve(device.leds.size());
            if (device.info.type == CDT_Keyboard) {
                const auto minmax_x = std::minmax_element(
                    device.leds.begin(),
                    device.leds.end(),
                    [](const CorsairLedPosition& left,
                       const CorsairLedPosition& right) {
                        return left.cx < right.cx;
                    });
                const auto minmax_y = std::minmax_element(
                    device.leds.begin(),
                    device.leds.end(),
                    [](const CorsairLedPosition& left,
                       const CorsairLedPosition& right) {
                        return left.cy < right.cy;
                    });
                const double x_range =
                    std::max(1.0, minmax_x.second->cx - minmax_x.first->cx);
                const double y_range =
                    std::max(1.0, minmax_y.second->cy - minmax_y.first->cy);
                for (const CorsairLedPosition& led : device.leds) {
                    const double x = (led.cx - minmax_x.first->cx) / x_range;
                    const double y = (led.cy - minmax_y.first->cy) / y_range;
                    frame.push_back(
                        watercolor_color(0.12 + x * 1.05 + y * 0.18, elapsed));
                }
            } else {
                const double denominator =
                    std::max<std::size_t>(1, device.leds.size() - 1);
                for (std::size_t index = 0; index < device.leds.size(); ++index) {
                    frame.push_back(watercolor_color(
                        0.54 + 0.68 * index / denominator,
                        elapsed));
                }
            }
            success = write_frame(device, frame, false) && success;
        }
        return success;
    }

    bool set_stranger(double elapsed) {
        bool success = true;
        for (const Device& device : devices_) {
            if (device.leds.empty()) {
                success = false;
                continue;
            }
            std::vector<Color> frame;
            frame.reserve(device.leds.size());
            if (device.info.type == CDT_Keyboard) {
                const auto minmax_x = std::minmax_element(
                    device.leds.begin(),
                    device.leds.end(),
                    [](const CorsairLedPosition& left,
                       const CorsairLedPosition& right) {
                        return left.cx < right.cx;
                    });
                const auto minmax_y = std::minmax_element(
                    device.leds.begin(),
                    device.leds.end(),
                    [](const CorsairLedPosition& left,
                       const CorsairLedPosition& right) {
                        return left.cy < right.cy;
                    });
                const double x_range =
                    std::max(1.0, minmax_x.second->cx - minmax_x.first->cx);
                const double y_range =
                    std::max(1.0, minmax_y.second->cy - minmax_y.first->cy);
                for (const CorsairLedPosition& led : device.leds) {
                    const double x = (led.cx - minmax_x.first->cx) / x_range;
                    const double y = (led.cy - minmax_y.first->cy) / y_range;
                    frame.push_back(stranger_things_color(
                        0.11 + x * 1.28 + y * 0.2,
                        elapsed,
                        static_cast<int>(std::lround(y * 5.0))));
                }
            } else {
                const double denominator =
                    std::max<std::size_t>(1, device.leds.size() - 1);
                for (std::size_t index = 0; index < device.leds.size(); ++index) {
                    frame.push_back(stranger_things_color(
                        0.57 + 0.82 * index / denominator,
                        elapsed,
                        8));
                }
            }
            success = write_frame(device, frame, false) && success;
        }
        return success;
    }

    std::size_t device_count() const {
        return devices_.size();
    }

private:
    struct Device {
        CorsairDeviceInfo info{};
        std::vector<CorsairLedPosition> leds;
    };

    bool write_frame(
        const Device& device,
        const std::vector<Color>& frame,
        bool log_result) {
        if (frame.size() != device.leds.size()) {
            return false;
        }
        std::vector<CorsairLedColor> colors;
        colors.reserve(device.leds.size());
        for (std::size_t index = 0; index < device.leds.size(); ++index) {
            const CorsairLedPosition& led = device.leds[index];
            const Color color = frame[index];
            colors.push_back({
                led.id,
                color.red,
                color.green,
                color.blue,
                255,
            });
        }
        const CorsairError error = CorsairSetLedColors(
            device.info.id,
            static_cast<int>(colors.size()),
            colors.data());
        if (log_result) {
            std::cout << "icue-color model=" << device.info.model
                      << " leds=" << colors.size() << " error=" << error
                      << std::endl;
        }
        return error == CE_Success;
    }

    bool enumerate_devices() {
        CorsairDeviceFilter filter{};
        filter.deviceTypeMask = CDT_Keyboard | CDT_Mousemat;
        CorsairDeviceInfo found[CORSAIR_DEVICE_COUNT_MAX]{};
        int count = 0;
        if (CorsairGetDevices(
                &filter,
                static_cast<int>(CORSAIR_DEVICE_COUNT_MAX),
                found,
                &count) != CE_Success) {
            return false;
        }

        devices_.clear();
        for (int index = 0; index < count; ++index) {
            const bool is_k70 = found[index].type == CDT_Keyboard &&
                                std::string(found[index].model) == "K70 MAX";
            const bool is_mousemat = include_mousemat_ &&
                                     found[index].type == CDT_Mousemat;
            if (!is_k70 && !is_mousemat) {
                continue;
            }
            Device device;
            device.info = found[index];
            device.leds.resize(CORSAIR_DEVICE_LEDCOUNT_MAX);
            int led_count = 0;
            if (CorsairGetLedPositions(
                    device.info.id,
                    static_cast<int>(device.leds.size()),
                    device.leds.data(),
                    &led_count) != CE_Success) {
                continue;
            }
            device.leds.resize(static_cast<std::size_t>(led_count));
            std::cout << "icue-device model=" << device.info.model
                      << " leds=" << device.leds.size() << std::endl;
            devices_.push_back(std::move(device));
        }
        return !devices_.empty();
    }

    bool include_mousemat_;
    std::vector<Device> devices_;
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

class ScimitarBackend {
public:
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
            hid_close(device_);
        }
    }

    bool set_color(Color color) {
        return set_colors({color, color, color});
    }

    bool set_colors(const std::array<Color, 3>& colors) {
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
            return false;
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
    bool prepared_ = false;
    std::chrono::steady_clock::time_point last_heartbeat_{};
};

struct ApplyResult {
    bool icue;
    bool g560;
    bool scimitar;
};

enum class AgentEffect {
    Static,
    Watercolor,
    StrangerThings,
};

ApplyResult apply_color(
    ICueBackend& icue,
    G560Backend& g560,
    ScimitarBackend& scimitar,
    Color color) {
    return {
        icue.set_color(color),
        g560.set_color(color),
        scimitar.set_color(color),
    };
}

ApplyResult apply_watercolor(
    ICueBackend& icue,
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
        icue.set_watercolor(elapsed),
        g560.set_colors(g560_colors),
        scimitar.set_colors(scimitar_colors),
    };
}

ApplyResult apply_stranger(
    ICueBackend& icue,
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
        icue.set_stranger(elapsed),
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
    ICueBackend& icue,
    G560Backend& g560,
    ScimitarBackend& scimitar,
    double elapsed) {
    if (effect == AgentEffect::Watercolor) {
        return apply_watercolor(icue, g560, scimitar, elapsed);
    }
    return apply_stranger(icue, g560, scimitar, elapsed);
}

std::string result_line(Color color, ApplyResult result) {
    std::ostringstream output;
    output << (result.icue && result.g560 && result.scimitar ? "OK" : "PARTIAL")
           << " color=" << color.hex()
           << " icue=" << (result.icue ? "ok" : "error")
           << " g560=" << (result.g560 ? "ok" : "error")
           << " scimitar=" << (result.scimitar ? "ok" : "error") << "\n";
    return output.str();
}

std::string effect_result_line(const std::string& effect, ApplyResult result) {
    std::ostringstream output;
    output << (result.icue && result.g560 && result.scimitar ? "OK" : "PARTIAL")
           << " effect=" << effect
           << " icue=" << (result.icue ? "ok" : "error")
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
    bool include_mousemat = false;
    AgentEffect effect = AgentEffect::Static;
    for (int index = 1; index < argc; ++index) {
        const std::string argument(argv[index]);
        if (argument == "--include-mm700") {
            include_mousemat = true;
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
                   "--effect watercolor|stranger-things] "
                   "[--include-mm700]"
                << std::endl;
            return 64;
        }
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

    ICueBackend icue(include_mousemat);
    G560Backend g560;
    ScimitarBackend scimitar;
    if (!icue.connect()) {
        std::cerr << "iCUE connection failed" << std::endl;
        return 2;
    }
    const auto effect_seconds = []() {
        return std::chrono::duration<double>(
            std::chrono::system_clock::now().time_since_epoch()).count();
    };
    ApplyResult last_result = effect == AgentEffect::Static
        ? apply_color(icue, g560, scimitar, current)
        : apply_effect(effect, icue, g560, scimitar, effect_seconds());
    std::cout << (effect == AgentEffect::Static
        ? result_line(current, last_result)
        : effect_result_line(effect_name(effect), last_result));

    const int server = create_server();
    if (server < 0) {
        std::cerr << "could not listen on 127.0.0.1:" << kListenPort << std::endl;
        return 3;
    }
    std::cout << "listening=127.0.0.1:" << kListenPort << std::endl;

    while (running.load() && icue.connected()) {
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
                            last_result = apply_color(icue, g560, scimitar, current);
                            response = result_line(current, last_result);
                        } else {
                            response = "ERROR color\n";
                        }
                    } else if (command == "EFFECT WATERCOLOR") {
                        effect = AgentEffect::Watercolor;
                        last_result = apply_effect(
                            effect,
                            icue,
                            g560,
                            scimitar,
                            effect_seconds());
                        response = effect_result_line(effect_name(effect), last_result);
                    } else if (command == "EFFECT STRANGER-THINGS") {
                        effect = AgentEffect::StrangerThings;
                        last_result = apply_effect(
                            effect,
                            icue,
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
                icue,
                g560,
                scimitar,
                effect_seconds());
            next_effect_frame = now + std::chrono::milliseconds(83);
        }
        if (!scimitar.heartbeat_if_due()) {
            std::cerr << "scimitar heartbeat failed" << std::endl;
        }
    }

    close(server);
    return running.load() ? 4 : 0;
}
