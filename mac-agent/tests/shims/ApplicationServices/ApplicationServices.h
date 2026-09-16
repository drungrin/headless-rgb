// SPDX-License-Identifier: GPL-3.0-or-later
//
// Declaration-only stand-in for the macOS ApplicationServices umbrella header,
// used to type-check agent.cpp off a Mac. It covers only the Accessibility and
// CoreGraphics event calls the agent makes.
//
// The types are deliberately opaque pointers with the real signatures, so a
// wrong argument type or count still fails to compile.

#pragma once

#include <cstddef>

typedef const void* CFTypeRef;
typedef const struct __CFString* CFStringRef;
typedef const struct __CFBoolean* CFBooleanRef;
typedef const struct __CFDictionary* CFDictionaryRef;
typedef const struct __CFAllocator* CFAllocatorRef;
typedef struct __CGEvent* CGEventRef;
typedef struct __CGEventSource* CGEventSourceRef;
typedef unsigned short CGKeyCode;
typedef unsigned int CGEventTapLocation;
typedef unsigned char Boolean;

struct CFDictionaryKeyCallBacks;
struct CFDictionaryValueCallBacks;

extern const CFAllocatorRef kCFAllocatorDefault;
extern const CFBooleanRef kCFBooleanTrue;
extern const CFStringRef kAXTrustedCheckOptionPrompt;
extern const CFDictionaryKeyCallBacks kCFCopyStringDictionaryKeyCallBacks;
extern const CFDictionaryValueCallBacks kCFTypeDictionaryValueCallBacks;

extern const CGEventTapLocation kCGHIDEventTap;

CFDictionaryRef CFDictionaryCreate(
    CFAllocatorRef allocator,
    const void** keys,
    const void** values,
    long numValues,
    const CFDictionaryKeyCallBacks* keyCallBacks,
    const CFDictionaryValueCallBacks* valueCallBacks);

void CFRelease(CFTypeRef cf);

Boolean AXIsProcessTrusted(void);
Boolean AXIsProcessTrustedWithOptions(CFDictionaryRef options);

CGEventRef CGEventCreateKeyboardEvent(
    CGEventSourceRef source, CGKeyCode virtualKey, bool keyDown);
void CGEventPost(CGEventTapLocation tap, CGEventRef event);
