// SPDX-License-Identifier: GPL-3.0-or-later
//
// Forced in with -include when type-checking off a Mac. SIGPIPE has no Windows
// equivalent and M_PI is not in the C++ standard, so both are supplied here
// rather than weakening agent.cpp for the sake of a check.

#pragma once

#ifndef SIGPIPE
#define SIGPIPE 13
#endif

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif
