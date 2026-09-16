// SPDX-License-Identifier: GPL-3.0-or-later
// Declaration-only stand-in; see sys/socket.h in this directory.
//
// FD_ZERO/FD_SET/FD_ISSET are written as real expressions rather than no-ops so
// that a misuse (wrong argument type, missing fd_set) still fails to compile.
// struct timeval comes from the host toolchain, which already defines it.

#pragma once

#include <sys/socket.h>
#include <time.h>

#define FD_SETSIZE 1024

typedef struct {
    unsigned int fds_bits[FD_SETSIZE / 32];
} fd_set;

#define FD_ZERO(set) \
    do { \
        for (int fd_index = 0; fd_index < FD_SETSIZE / 32; ++fd_index) { \
            (set)->fds_bits[fd_index] = 0; \
        } \
    } while (0)
#define FD_SET(fd, set) ((set)->fds_bits[(fd) / 32] |= (1u << ((fd) % 32)))
#define FD_ISSET(fd, set) (((set)->fds_bits[(fd) / 32] & (1u << ((fd) % 32))) != 0)

int select(int nfds, fd_set* readfds, fd_set* writefds, fd_set* errorfds, struct timeval* timeout);
