#include <atomic>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

#include <iCUESDK.h>

namespace {

std::atomic<int> session_state{CSS_Closed};

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

void on_session_state_changed(void*, const CorsairSessionStateChanged* event) {
    if (event == nullptr) {
        return;
    }
    session_state.store(event->state);
    std::cout << "session-state=" << event->state
              << " sdk=" << event->details.clientVersion.major << "."
              << event->details.clientVersion.minor << "."
              << event->details.clientVersion.patch
              << " server=" << event->details.serverVersion.major << "."
              << event->details.serverVersion.minor << "."
              << event->details.serverVersion.patch
              << " icue=" << event->details.serverHostVersion.major << "."
              << event->details.serverHostVersion.minor << "."
              << event->details.serverHostVersion.patch << std::endl;
}

const char* device_type_name(CorsairDeviceType type) {
    switch (type) {
        case CDT_Keyboard:
            return "keyboard";
        case CDT_Mouse:
            return "mouse";
        case CDT_Mousemat:
            return "mousemat";
        case CDT_Headset:
            return "headset";
        case CDT_HeadsetStand:
            return "headset-stand";
        case CDT_FanLedController:
            return "fan-controller";
        case CDT_LedController:
            return "led-controller";
        case CDT_MemoryModule:
            return "memory";
        case CDT_Cooler:
            return "cooler";
        case CDT_Motherboard:
            return "motherboard";
        case CDT_GraphicsCard:
            return "graphics-card";
        case CDT_Touchbar:
            return "touchbar";
        case CDT_GameController:
            return "game-controller";
        default:
            return "unknown";
    }
}

}  // namespace

int main(int argc, char** argv) {
    Color color{};
    const bool set_color = argc == 3 && std::string(argv[1]) == "--color";
    if (argc != 1 && (!set_color || !parse_color(argv[2], color))) {
        std::cerr << "usage: icue-probe [--color RRGGBB]" << std::endl;
        return 64;
    }

    const CorsairError connect_error =
        CorsairConnect(on_session_state_changed, nullptr);
    if (connect_error != CE_Success) {
        std::cerr << "CorsairConnect error=" << connect_error << std::endl;
        return 1;
    }

    const auto deadline = std::chrono::steady_clock::now() +
                          std::chrono::seconds(15);
    while (session_state.load() != CSS_Connected &&
           std::chrono::steady_clock::now() < deadline) {
        const int state = session_state.load();
        if (state == CSS_ConnectionRefused || state == CSS_ConnectionLost) {
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }

    if (session_state.load() != CSS_Connected) {
        std::cerr << "iCUE SDK did not connect; final-state="
                  << session_state.load() << std::endl;
        CorsairDisconnect();
        return 2;
    }

    CorsairDeviceFilter filter{};
    filter.deviceTypeMask = CDT_All;
    CorsairDeviceInfo devices[CORSAIR_DEVICE_COUNT_MAX]{};
    int device_count = 0;
    const CorsairError devices_error = CorsairGetDevices(
        &filter,
        static_cast<int>(CORSAIR_DEVICE_COUNT_MAX),
        devices,
        &device_count);
    if (devices_error != CE_Success) {
        std::cerr << "CorsairGetDevices error=" << devices_error << std::endl;
        CorsairDisconnect();
        return 3;
    }

    std::cout << "device-count=" << device_count << std::endl;
    if (set_color) {
        const CorsairError priority_error = CorsairSetLayerPriority(255);
        if (priority_error != CE_Success) {
            std::cerr << "CorsairSetLayerPriority error=" << priority_error
                      << std::endl;
            CorsairDisconnect();
            return 4;
        }
    }
    for (int index = 0; index < device_count; ++index) {
        const CorsairDeviceInfo& device = devices[index];
        CorsairLedPosition leds[CORSAIR_DEVICE_LEDCOUNT_MAX]{};
        int led_count = 0;
        const CorsairError leds_error = CorsairGetLedPositions(
            device.id,
            static_cast<int>(CORSAIR_DEVICE_LEDCOUNT_MAX),
            leds,
            &led_count);
        std::cout << "device[" << index << "]"
                  << " type=" << device_type_name(device.type)
                  << " model=" << device.model
                  << " serial=" << device.serial
                  << " id=" << device.id
                  << " reported-leds=" << device.ledCount
                  << " enumerated-leds="
                  << (leds_error == CE_Success ? led_count : -1)
                  << " channels=" << device.channelCount
                  << " led-query-error=" << leds_error << std::endl;
        const bool selected_for_color =
            device.type == CDT_Keyboard || device.type == CDT_Mouse;
        if (set_color && leds_error == CE_Success && selected_for_color) {
            std::vector<CorsairLedColor> colors;
            colors.reserve(static_cast<std::size_t>(led_count));
            for (int led = 0; led < led_count; ++led) {
                colors.push_back(CorsairLedColor{
                    leds[led].id,
                    color.red,
                    color.green,
                    color.blue,
                    255,
                });
            }
            CorsairError color_error = CorsairSetLedColors(
                device.id,
                static_cast<int>(colors.size()),
                colors.data());
            if (color_error == CE_NoControl) {
                const CorsairError control_error = CorsairRequestControl(
                    device.id,
                    CAL_ExclusiveLightingControl);
                std::cout << "device[" << index << "] control-error="
                          << control_error << std::endl;
                if (control_error == CE_Success) {
                    color_error = CorsairSetLedColors(
                        device.id,
                        static_cast<int>(colors.size()),
                        colors.data());
                }
            }
            std::cout << "device[" << index << "] color-error="
                      << color_error << std::endl;
        } else if (set_color && !selected_for_color) {
            std::cout << "device[" << index
                      << "] color-skipped=outside-authorized-scope" << std::endl;
        }
    }

    if (set_color) {
        std::cout << "holding SDK color for 8 seconds" << std::endl;
        std::this_thread::sleep_for(std::chrono::seconds(8));
    }

    CorsairDisconnect();
    return 0;
}
