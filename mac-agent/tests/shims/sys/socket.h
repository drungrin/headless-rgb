// SPDX-License-Identifier: GPL-3.0-or-later
//
// Declaration-only stand-in for the BSD sockets API, used to type-check
// agent.cpp on a machine without POSIX headers. It declares exactly the
// surface agent.cpp calls, with the real signatures, so a wrong argument type
// or count still fails to compile.
//
// This is NOT a working sockets library: the agent only runs on macOS.

#pragma once

#include <cstddef>

typedef unsigned int socklen_t;
typedef unsigned short sa_family_t;

#ifndef _SSIZE_T_DEFINED
#define _SSIZE_T_DEFINED
typedef long long ssize_t;
#endif

#define AF_INET 2
#define SOCK_STREAM 1
#define SOL_SOCKET 0xffff
#define SO_REUSEADDR 0x0004
#define SO_RCVTIMEO 0x1006
#define IPPROTO_TCP 6

struct sockaddr {
    sa_family_t sa_family;
    char sa_data[14];
};

int socket(int domain, int type, int protocol);
int setsockopt(int fd, int level, int option, const void* value, socklen_t length);
int bind(int fd, const struct sockaddr* address, socklen_t length);
int listen(int fd, int backlog);
int accept(int fd, struct sockaddr* address, socklen_t* length);
ssize_t recv(int fd, void* buffer, size_t length, int flags);
ssize_t send(int fd, const void* buffer, size_t length, int flags);
