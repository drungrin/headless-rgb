// SPDX-License-Identifier: GPL-2.0-or-later
// G560 report format derived from OpenRGB's LogitechG560Controller.

#include <array>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <thread>

#include <hidapi/hidapi.h>

namespace {

constexpr unsigned short kLogitechVendorId = 0x046d;
constexpr unsigned short kG560ProductId = 0x0a78;
constexpr unsigned short kLightsyncUsagePage = 0xff43;
constexpr unsigned short kG560Usage = 0x0202;
constexpr int kZoneCount = 4;

struct Color {
    unsigned char red;
    unsigned char green;
    unsigned char blue;
};

bool parse_color(const std::string& value, Color& color) {
    std::string hex = value;
    if (!hex.empty() && hex.front() == '#') {
        hex.erase(0, 1);
    }
    if (hex.size() != 6) {
        return false;
    }
    char* end = nullptr;
    const unsigned long rgb = std::strtoul(hex.c_str(), &end, 16);
    if (end == nullptr || *end != '\0') {
        return false;
    }
    color.red = static_cast<unsigned char>((rgb >> 16) & 0xff);
    color.green = static_cast<unsigned char>((rgb >> 8) & 0xff);
    color.blue = static_cast<unsigned char>(rgb & 0xff);
    return true;
}

std::string narrow(const wchar_t* value) {
    if (value == nullptr) {
        return "";
    }
    std::mbstate_t state{};
    const wchar_t* source = value;
    const std::size_t length = std::wcsrtombs(nullptr, &source, 0, &state);
    if (length == static_cast<std::size_t>(-1)) {
        return "<unprintable>";
    }
    std::string result(length, '\0');
    state = {};
    source = value;
    std::wcsrtombs(result.data(), &source, result.size(), &state);
    return result;
}

bool write_report(hid_device* device, const std::array<unsigned char, 20>& report) {
    for (int attempt = 0; attempt < 3; ++attempt) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
        const int written = hid_write(device, report.data(), report.size());
        if (written > 0) {
            std::array<unsigned char, 33> response{};
            hid_read_timeout(device, response.data(), response.size(), 100);
            return true;
        }
    }
    return false;
}

bool set_zone(hid_device* device, unsigned char zone, Color color) {
    std::array<unsigned char, 20> direct{};
    direct[0] = 0x11;
    direct[1] = 0xff;
    direct[2] = 0x04;
    direct[3] = 0xca;
    direct[4] = zone;
    if (!write_report(device, direct)) {
        return false;
    }

    std::array<unsigned char, 20> color_report{};
    color_report[0] = 0x11;
    color_report[1] = 0xff;
    color_report[2] = 0x04;
    color_report[3] = 0x3a;
    color_report[4] = zone;
    color_report[5] = 0x01;
    color_report[6] = color.red;
    color_report[7] = color.green;
    color_report[8] = color.blue;
    color_report[9] = 0x02;
    return write_report(device, color_report);
}

}  // namespace

int main(int argc, char** argv) {
    Color color{};
    const bool set_color = argc == 3 && std::string(argv[1]) == "--color";
    if (argc != 1 && (!set_color || !parse_color(argv[2], color))) {
        std::cerr << "usage: g560-probe [--color RRGGBB]" << std::endl;
        return 64;
    }

    if (hid_init() != 0) {
        std::cerr << "hid_init failed" << std::endl;
        return 1;
    }

    hid_device_info* devices = hid_enumerate(kLogitechVendorId, kG560ProductId);
    const char* selected_path = nullptr;
    for (hid_device_info* info = devices; info != nullptr; info = info->next) {
        std::cout << "interface path=" << info->path
                  << " interface=" << info->interface_number
                  << " usage-page=0x" << std::hex << info->usage_page
                  << " usage=0x" << info->usage << std::dec
                  << " product=" << narrow(info->product_string)
                  << " serial=" << narrow(info->serial_number) << std::endl;
        if (info->usage_page == kLightsyncUsagePage &&
            info->usage == kG560Usage) {
            selected_path = info->path;
        }
    }

    if (selected_path == nullptr) {
        std::cerr << "G560 Lightsync HID interface not found" << std::endl;
        hid_free_enumeration(devices);
        hid_exit();
        return 2;
    }
    const std::string path(selected_path);
    hid_free_enumeration(devices);

    hid_device* device = hid_open_path(path.c_str());
    if (device == nullptr) {
        std::cerr << "could not open G560 interface: "
                  << narrow(hid_error(nullptr)) << std::endl;
        hid_exit();
        return 3;
    }
    std::cout << "selected=" << path << std::endl;

    if (set_color) {
        for (int zone = 0; zone < kZoneCount; ++zone) {
            if (!set_zone(device, static_cast<unsigned char>(zone), color)) {
                std::cerr << "failed writing zone " << zone << ": "
                          << narrow(hid_error(device)) << std::endl;
                hid_close(device);
                hid_exit();
                return 4;
            }
        }
        std::cout << "color-set=" << argv[2] << " zones=" << kZoneCount
                  << std::endl;
    }

    hid_close(device);
    hid_exit();
    return 0;
}
