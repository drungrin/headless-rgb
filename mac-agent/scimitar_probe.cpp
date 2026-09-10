// SPDX-License-Identifier: GPL-3.0-or-later
// Scimitar Wireless SE report format derived from OpenLinkHub.

#include <array>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <thread>

#include <hidapi/hidapi.h>
#include <hidapi/hidapi_darwin.h>

namespace {

constexpr unsigned short kVendor = 0x1b1c;
constexpr unsigned short kProduct = 0x2b00;
constexpr int kControlInterface = 1;
constexpr unsigned char kMouseEndpoint = 0x09;

struct Color {
    unsigned char red;
    unsigned char green;
    unsigned char blue;
};

bool parse_color(std::string value, Color& color) {
    if (!value.empty() && value.front() == '#') {
        value.erase(0, 1);
    }
    if (value.size() != 6) {
        return false;
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

bool transfer(
    hid_device* device,
    const unsigned char* endpoint,
    std::size_t endpoint_size,
    const unsigned char* payload = nullptr,
    std::size_t payload_size = 0) {
    std::array<unsigned char, 65> output{};
    if (2 + endpoint_size + payload_size > output.size()) {
        return false;
    }
    output[1] = kMouseEndpoint;
    std::memcpy(output.data() + 2, endpoint, endpoint_size);
    if (payload != nullptr && payload_size > 0) {
        std::memcpy(output.data() + 2 + endpoint_size, payload, payload_size);
    }
    if (hid_write(device, output.data(), output.size()) < 0) {
        return false;
    }
    std::array<unsigned char, 64> response{};
    return hid_read_timeout(device, response.data(), response.size(), 500) >= 0;
}

bool set_color(hid_device* device, Color color, bool software) {
    constexpr std::array<unsigned char, 4> software_mode{0x01, 0x03, 0x00, 0x02};
    constexpr std::array<unsigned char, 3> open_endpoint{0x0d, 0x00, 0x01};
    constexpr std::array<unsigned char, 2> write_color{0x06, 0x00};
    if ((software && !transfer(device, software_mode.data(), software_mode.size())) ||
        !transfer(device, open_endpoint.data(), open_endpoint.size())) {
        return false;
    }

    std::array<unsigned char, 16> payload{};
    payload[0] = 12;
    for (std::size_t channel = 0; channel < 3; ++channel) {
        payload[4 + channel] = color.red;
        payload[8 + channel] = color.green;
        payload[12 + channel] = color.blue;
    }
    return transfer(
        device,
        write_color.data(),
        write_color.size(),
        payload.data(),
        payload.size());
}

}  // namespace

int main(int argc, char** argv) {
    Color color{};
    const bool apply = argc == 3 && std::string(argv[1]) == "--color";
    const bool hardware_color =
        argc == 3 && std::string(argv[1]) == "--hardware-color";
    const bool hardware_only = argc == 2 && std::string(argv[1]) == "--hardware";
    if (argc != 1 && !hardware_only &&
        (!(apply || hardware_color) || !parse_color(argv[2], color))) {
        std::cerr
            << "usage: scimitar-probe "
               "[--color RRGGBB | --hardware-color RRGGBB | --hardware]"
            << std::endl;
        return 64;
    }

    hid_darwin_set_open_exclusive(0);
    if (hid_init() != 0) {
        return 1;
    }
    hid_device_info* devices = hid_enumerate(kVendor, kProduct);
    std::string selected_path;
    for (hid_device_info* info = devices; info != nullptr; info = info->next) {
        std::cout << "path=" << info->path
                  << " interface=" << info->interface_number
                  << " usage-page=0x" << std::hex << info->usage_page
                  << " usage=0x" << info->usage << std::dec << std::endl;
        if (info->interface_number == kControlInterface) {
            selected_path = info->path;
        }
    }
    hid_free_enumeration(devices);
    if (selected_path.empty()) {
        std::cerr << "Scimitar control interface not found" << std::endl;
        hid_exit();
        return 2;
    }
    if (!apply && !hardware_color && !hardware_only) {
        std::cout << "selected=" << selected_path << std::endl;
        hid_exit();
        return 0;
    }

    hid_device* device = hid_open_path(selected_path.c_str());
    if (device == nullptr) {
        std::cerr << "could not open Scimitar control interface" << std::endl;
        hid_exit();
        return 3;
    }
    constexpr std::array<unsigned char, 4> hardware_mode{0x01, 0x03, 0x00, 0x01};
    if (hardware_only) {
        const bool success = transfer(
            device,
            hardware_mode.data(),
            hardware_mode.size());
        hid_close(device);
        hid_exit();
        return success ? 0 : 4;
    }
    if (hardware_color &&
        !transfer(device, hardware_mode.data(), hardware_mode.size())) {
        hid_close(device);
        hid_exit();
        return 4;
    }
    const bool success = set_color(device, color, apply);
    if (success) {
        std::cout << "color-set=" << argv[2] << " holding=8s" << std::endl;
        std::this_thread::sleep_for(std::chrono::seconds(8));
    }
    transfer(device, hardware_mode.data(), hardware_mode.size());
    hid_close(device);
    hid_exit();
    return success ? 0 : 4;
}
