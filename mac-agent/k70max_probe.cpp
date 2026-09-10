// SPDX-License-Identifier: GPL-3.0-or-later
// Protocol framing derives from OpenLinkHub's K70 MAX implementation.

#include <array>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <thread>

#include <hidapi/hidapi.h>
#include <hidapi/hidapi_darwin.h>

namespace {

constexpr unsigned short kVendor = 0x1b1c;
constexpr unsigned short kProduct = 0x1bc0;
constexpr int kInterface = 1;
constexpr unsigned char kCommand = 0x08;
constexpr std::size_t kReportSize = 129;
constexpr std::size_t kColorChannels = 142;
constexpr std::size_t kColorDataSize = 428;
constexpr std::size_t kChunkSize = 125;

struct Color {
    unsigned char red;
    unsigned char green;
    unsigned char blue;
};

bool parse_color(const std::string& value, Color& color) {
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
    std::array<unsigned char, kReportSize> output{};
    if (2 + endpoint_size + payload_size > output.size()) {
        return false;
    }
    output[1] = kCommand;
    std::memcpy(output.data() + 2, endpoint, endpoint_size);
    if (payload != nullptr && payload_size > 0) {
        std::memcpy(output.data() + 2 + endpoint_size, payload, payload_size);
    }
    if (hid_write(device, output.data(), output.size()) < 0) {
        return false;
    }
    std::array<unsigned char, 128> response{};
    return hid_read_timeout(device, response.data(), response.size(), 500) >= 0;
}

bool write_color(hid_device* device, Color color) {
    std::array<unsigned char, kColorDataSize> color_data{};
    for (std::size_t channel = 0; channel < kColorChannels; ++channel) {
        color_data[channel * 3] = color.red;
        color_data[channel * 3 + 1] = color.green;
        color_data[channel * 3 + 2] = color.blue;
    }

    std::array<unsigned char, 434> packet{};
    packet[0] = static_cast<unsigned char>(kColorDataSize & 0xff);
    packet[1] = static_cast<unsigned char>((kColorDataSize >> 8) & 0xff);
    packet[4] = 0x12;
    packet[5] = 0x00;
    std::memcpy(packet.data() + 6, color_data.data(), color_data.size());

    constexpr std::array<unsigned char, 2> first_endpoint{0x06, 0x01};
    constexpr std::array<unsigned char, 2> next_endpoint{0x07, 0x01};
    for (std::size_t offset = 0; offset < packet.size(); offset += kChunkSize) {
        const std::size_t remaining = packet.size() - offset;
        const std::size_t size = remaining < kChunkSize ? remaining : kChunkSize;
        const auto& endpoint = offset == 0 ? first_endpoint : next_endpoint;
        if (!transfer(
                device,
                endpoint.data(),
                endpoint.size(),
                packet.data() + offset,
                size)) {
            return false;
        }
    }
    return true;
}

bool prepare(hid_device* device) {
    constexpr std::array<unsigned char, 4> software{0x01, 0x03, 0x00, 0x02};
    constexpr std::array<unsigned char, 3> activate_keys{0x0d, 0x01, 0x22};
    constexpr std::array<unsigned char, 3> activate_bar{0x0d, 0x00, 0x2e};
    return transfer(device, software.data(), software.size()) &&
           transfer(device, activate_keys.data(), activate_keys.size()) &&
           transfer(device, activate_bar.data(), activate_bar.size());
}

}  // namespace

int main(int argc, char** argv) {
    Color color{};
    const bool apply = argc == 3 && std::string(argv[1]) == "--color";
    if (argc != 1 && (!apply || !parse_color(argv[2], color))) {
        std::cerr << "usage: k70max-probe [--color RRGGBB]" << std::endl;
        return 64;
    }

    hid_darwin_set_open_exclusive(0);
    if (hid_init() != 0) {
        return 1;
    }
    hid_device_info* devices = hid_enumerate(kVendor, kProduct);
    std::string path;
    for (hid_device_info* info = devices; info != nullptr; info = info->next) {
        std::cout << "interface=" << info->interface_number
                  << " usage-page=0x" << std::hex << info->usage_page
                  << " usage=0x" << info->usage << std::dec << std::endl;
        if (info->interface_number == kInterface) {
            path = info->path;
        }
    }
    hid_free_enumeration(devices);
    if (path.empty()) {
        hid_exit();
        return 2;
    }
    if (!apply) {
        hid_exit();
        return 0;
    }

    hid_device* device = hid_open_path(path.c_str());
    if (device == nullptr) {
        hid_exit();
        return 3;
    }
    const bool success = prepare(device) && write_color(device, color);
    std::cout << "color-set=" << argv[2] << " holding=8s" << std::endl;
    std::this_thread::sleep_for(std::chrono::seconds(8));
    constexpr std::array<unsigned char, 4> hardware{0x01, 0x03, 0x00, 0x01};
    transfer(device, hardware.data(), hardware.size());
    hid_close(device);
    hid_exit();
    return success ? 0 : 4;
}
