// SPDX-License-Identifier: GPL-3.0-or-later
//
// Declaration-only stand-in for hidapi, used to type-check agent.cpp on a
// machine that is not a Mac. It declares exactly the surface agent.cpp calls
// and nothing else, so a call the real library does not offer still fails to
// compile.
//
// This is NOT a working hidapi: the agent still only runs on macOS, where
// install.sh links the real Homebrew library.

#pragma once

#include <cstddef>
#include <wchar.h>

struct hid_device_;
typedef struct hid_device_ hid_device;

struct hid_device_info {
    char* path;
    unsigned short vendor_id;
    unsigned short product_id;
    wchar_t* serial_number;
    unsigned short release_number;
    wchar_t* manufacturer_string;
    wchar_t* product_string;
    unsigned short usage_page;
    unsigned short usage;
    int interface_number;
    struct hid_device_info* next;
};

int hid_init(void);
int hid_exit(void);

struct hid_device_info* hid_enumerate(unsigned short vendor_id, unsigned short product_id);
void hid_free_enumeration(struct hid_device_info* devs);

hid_device* hid_open_path(const char* path);
void hid_close(hid_device* dev);

int hid_write(hid_device* dev, const unsigned char* data, size_t length);
int hid_read(hid_device* dev, unsigned char* data, size_t length);
int hid_read_timeout(hid_device* dev, unsigned char* data, size_t length, int milliseconds);
int hid_set_nonblocking(hid_device* dev, int nonblock);
