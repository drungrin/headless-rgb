// SPDX-License-Identifier: GPL-3.0-or-later
// Slipstream input parsing derives from OpenLinkHub.

#include <array>
#include <chrono>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <string>

#include <hidapi/hidapi.h>
#include <hidapi/hidapi_darwin.h>

namespace {

constexpr unsigned short kVendor = 0x1b1c;
constexpr unsigned short kProduct = 0x2b00;

std::string path_for_interface(int target) {
    hid_device_info* devices = hid_enumerate(kVendor, kProduct);
    std::string path;
    for (hid_device_info* info = devices; info != nullptr; info = info->next) {
        if (info->interface_number == target) {
            path = info->path;
            break;
        }
    }
    hid_free_enumeration(devices);
    return path;
}

bool transfer(hid_device* device, const std::array<unsigned char, 4>& command) {
    std::array<unsigned char, 65> output{};
    output[1] = 0x09;
    std::memcpy(output.data() + 2, command.data(), command.size());
    if (hid_write(device, output.data(), output.size()) < 0) {
        return false;
    }
    std::array<unsigned char, 64> response{};
    return hid_read_timeout(device, response.data(), response.size(), 500) >= 0;
}

}  // namespace

int main() {
    hid_darwin_set_open_exclusive(0);
    if (hid_init() != 0) {
        return 1;
    }
    const std::string control_path = path_for_interface(1);
    const std::string listener_path = path_for_interface(2);
    if (control_path.empty() || listener_path.empty()) {
        hid_exit();
        return 2;
    }
    hid_device* control = hid_open_path(control_path.c_str());
    hid_device* listener = hid_open_path(listener_path.c_str());
    if (control == nullptr || listener == nullptr) {
        if (control != nullptr) {
            hid_close(control);
        }
        if (listener != nullptr) {
            hid_close(listener);
        }
        hid_exit();
        return 3;
    }

    constexpr std::array<unsigned char, 4> software{0x01, 0x03, 0x00, 0x02};
    constexpr std::array<unsigned char, 4> hardware{0x01, 0x03, 0x00, 0x01};
    if (!transfer(control, software)) {
        hid_close(listener);
        hid_close(control);
        hid_exit();
        return 4;
    }
    std::cout << "capturing=15s" << std::endl;
    const auto deadline = std::chrono::steady_clock::now() +
                          std::chrono::seconds(15);
    while (std::chrono::steady_clock::now() < deadline) {
        std::array<unsigned char, 64> data{};
        const int count = hid_read_timeout(listener, data.data(), data.size(), 100);
        if (count <= 0) {
            continue;
        }
        std::cout << "report";
        for (int index = 0; index < count; ++index) {
            std::cout << " " << std::hex << std::setw(2) << std::setfill('0')
                      << static_cast<int>(data[static_cast<std::size_t>(index)]);
        }
        std::cout << std::dec;
        if (count >= 6 && data[0] == 2 &&
            (data[1] == 0x02 || data[1] == 0x05 || data[1] == 0x09)) {
            const unsigned int mask =
                static_cast<unsigned int>(data[2]) |
                (static_cast<unsigned int>(data[3]) << 8) |
                (static_cast<unsigned int>(data[4]) << 16) |
                (static_cast<unsigned int>(data[5]) << 24);
            std::cout << " mask=0x" << std::hex << mask << std::dec;
        }
        std::cout << std::endl;
    }
    transfer(control, hardware);
    hid_close(listener);
    hid_close(control);
    hid_exit();
    return 0;
}
