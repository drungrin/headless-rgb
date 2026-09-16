// SPDX-License-Identifier: GPL-3.0-or-later
// Declaration-only stand-in; see sys/socket.h in this directory.
//
// The host toolchain already provides errno; only the values agent.cpp compares
// against need pinning, and the real header's definition is left alone.

#pragma once

#include_next <errno.h>

#undef EAGAIN
#undef EWOULDBLOCK
#undef EINTR
#define EAGAIN 35
#define EWOULDBLOCK EAGAIN
#define EINTR 4
