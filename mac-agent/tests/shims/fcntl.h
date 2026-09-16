// SPDX-License-Identifier: GPL-3.0-or-later
// Declaration-only stand-in; see sys/socket.h in this directory.

#pragma once

#define O_NONBLOCK 0x0004
#define F_GETFL 3
#define F_SETFL 4

int fcntl(int fd, int command, ...);
