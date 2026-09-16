// SPDX-License-Identifier: GPL-3.0-or-later
// Declaration-only stand-in; see sys/socket.h in this directory.

#pragma once

#include <sys/socket.h>

#define INADDR_LOOPBACK 0x7f000001

typedef unsigned short in_port_t;
typedef unsigned int in_addr_t;

struct in_addr {
    in_addr_t s_addr;
};

struct sockaddr_in {
    sa_family_t sin_family;
    in_port_t sin_port;
    struct in_addr sin_addr;
    char sin_zero[8];
};
