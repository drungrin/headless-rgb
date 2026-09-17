"""Dump the Slipstream vendor reports that carry the Scimitar side buttons.

Windows counterpart of mac-agent/scimitar_input_probe.cpp. It opens the
dongle's vendor interface read-only and prints every input report, so the
side-button bitmask can be observed while SignalRGB drives the RGB.

    python tools/scimitar_input_probe.py [seconds]
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

VENDOR = 0x1B1C
SLIPSTREAM = 0x2B00
SIDE_BUTTON_MASK = 0x0001FFE0

DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10
GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x01
FILE_SHARE_WRITE = 0x02
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
WAIT_TIMEOUT = 0x102

setupapi = ctypes.WinDLL("setupapi")
hid = ctypes.WinDLL("hid")
kernel32 = ctypes.WinDLL("kernel32")


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.POINTER(ctypes.c_ulonglong)),
    ]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Size", wintypes.ULONG),
        ("VendorID", ctypes.c_ushort),
        ("ProductID", ctypes.c_ushort),
        ("VersionNumber", ctypes.c_ushort),
    ]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", ctypes.c_ushort),
        ("UsagePage", ctypes.c_ushort),
        ("InputReportByteLength", ctypes.c_ushort),
        ("OutputReportByteLength", ctypes.c_ushort),
        ("FeatureReportByteLength", ctypes.c_ushort),
        ("Reserved", ctypes.c_ushort * 17),
        ("NumberLinkCollectionNodes", ctypes.c_ushort),
        ("NumberInputButtonCaps", ctypes.c_ushort),
        ("NumberInputValueCaps", ctypes.c_ushort),
        ("NumberInputDataIndices", ctypes.c_ushort),
        ("NumberOutputButtonCaps", ctypes.c_ushort),
        ("NumberOutputValueCaps", ctypes.c_ushort),
        ("NumberOutputDataIndices", ctypes.c_ushort),
        ("NumberFeatureButtonCaps", ctypes.c_ushort),
        ("NumberFeatureValueCaps", ctypes.c_ushort),
        ("NumberFeatureDataIndices", ctypes.c_ushort),
    ]


class OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.POINTER(ctypes.c_ulonglong)),
        ("InternalHigh", ctypes.POINTER(ctypes.c_ulonglong)),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


# Without explicit prototypes ctypes returns a 32-bit int and truncates every
# handle these calls hand back.
setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
setupapi.SetupDiGetClassDevsW.argtypes = [
    ctypes.POINTER(GUID),
    wintypes.LPCWSTR,
    wintypes.HWND,
    wintypes.DWORD,
]
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.POINTER(GUID),
    wintypes.DWORD,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
]
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.c_void_p,
]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.CreateEventW.argtypes = [
    ctypes.c_void_p,
    wintypes.BOOL,
    wintypes.BOOL,
    wintypes.LPCWSTR,
]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CancelIo.argtypes = [wintypes.HANDLE]
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.ReadFile.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(OVERLAPPED),
]
kernel32.GetOverlappedResult.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(OVERLAPPED),
    ctypes.POINTER(wintypes.DWORD),
    wintypes.BOOL,
]
hid.HidD_GetAttributes.argtypes = [wintypes.HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]
hid.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]


def interface_paths() -> list[str]:
    """Every present HID interface path, in enumeration order."""
    guid = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(guid))
    collection = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE
    )
    if collection == INVALID_HANDLE_VALUE:
        return []

    paths: list[str] = []
    interface = SP_DEVICE_INTERFACE_DATA()
    interface.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
    index = 0
    while setupapi.SetupDiEnumDeviceInterfaces(
        collection, None, ctypes.byref(guid), index, ctypes.byref(interface)
    ):
        index += 1
        size = wintypes.DWORD()
        setupapi.SetupDiGetDeviceInterfaceDetailW(
            collection, ctypes.byref(interface), None, 0, ctypes.byref(size), None
        )
        if size.value == 0:
            continue
        buffer = ctypes.create_string_buffer(size.value)
        # SP_DEVICE_INTERFACE_DETAIL_DATA_W: cbSize then a WCHAR path.
        ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD))[0] = (
            8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
        )
        if setupapi.SetupDiGetDeviceInterfaceDetailW(
            collection,
            ctypes.byref(interface),
            buffer,
            size.value,
            ctypes.byref(size),
            None,
        ):
            paths.append(ctypes.wstring_at(ctypes.addressof(buffer) + 4))

    setupapi.SetupDiDestroyDeviceInfoList(collection)
    return paths


def open_path(path: str) -> wintypes.HANDLE | None:
    handle = kernel32.CreateFileW(
        path,
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        FILE_FLAG_OVERLAPPED,
        None,
    )
    return None if handle == INVALID_HANDLE_VALUE else handle


def describe(handle: wintypes.HANDLE) -> tuple[HIDD_ATTRIBUTES, HIDP_CAPS] | None:
    attributes = HIDD_ATTRIBUTES()
    attributes.Size = ctypes.sizeof(HIDD_ATTRIBUTES)
    if not hid.HidD_GetAttributes(handle, ctypes.byref(attributes)):
        return None
    preparsed = ctypes.c_void_p()
    if not hid.HidD_GetPreparsedData(handle, ctypes.byref(preparsed)):
        return None
    caps = HIDP_CAPS()
    hid.HidP_GetCaps(preparsed, ctypes.byref(caps))
    hid.HidD_FreePreparsedData(preparsed)
    return attributes, caps


def find_listener() -> tuple[wintypes.HANDLE, int] | None:
    """The dongle interface that carries button and battery notifications."""
    for path in interface_paths():
        lowered = path.lower()
        if f"vid_{VENDOR:04x}" not in lowered or f"pid_{SLIPSTREAM:04x}" not in lowered:
            continue
        if "mi_02" not in lowered:
            continue
        handle = open_path(path)
        if handle is None:
            print(f"busy or denied: {path}")
            continue
        details = describe(handle)
        if details is None:
            kernel32.CloseHandle(handle)
            continue
        attributes, caps = details
        print(
            f"opened {path}\n"
            f"  usage_page=0x{caps.UsagePage:04x} usage=0x{caps.Usage:04x} "
            f"input_length={caps.InputReportByteLength}"
        )
        if caps.UsagePage != 0xFF42:
            kernel32.CloseHandle(handle)
            continue
        return handle, caps.InputReportByteLength
    return None


def read_report(handle: wintypes.HANDLE, length: int, timeout_ms: int) -> bytes | None:
    buffer = ctypes.create_string_buffer(length)
    read = wintypes.DWORD()
    overlapped = OVERLAPPED()
    overlapped.hEvent = kernel32.CreateEventW(None, True, False, None)
    try:
        if kernel32.ReadFile(
            handle, buffer, length, ctypes.byref(read), ctypes.byref(overlapped)
        ):
            return buffer.raw[: read.value]
        if kernel32.WaitForSingleObject(overlapped.hEvent, timeout_ms) == WAIT_TIMEOUT:
            kernel32.CancelIo(handle)
            return None
        if not kernel32.GetOverlappedResult(
            handle, ctypes.byref(overlapped), ctypes.byref(read), True
        ):
            return None
        return buffer.raw[: read.value]
    finally:
        kernel32.CloseHandle(overlapped.hEvent)


def main() -> int:
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    found = find_listener()
    if found is None:
        print("no Slipstream vendor interface found")
        return 1
    handle, length = found
    print(f"capturing={seconds}s — press the side buttons now")
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            report = read_report(handle, length, 200)
            if not report:
                continue
            trimmed = report.rstrip(b"\x00") or report[:1]
            line = " ".join(f"{byte:02x}" for byte in trimmed)
            if len(report) >= 8 and report[0] == 0x00 and report[2] == 0x02:
                mask = int.from_bytes(report[3:7], "little")
                line += f"  macro_mask=0x{mask:08x} side=0x{mask & SIDE_BUTTON_MASK:08x}"
            print(f"report {line}")
    finally:
        kernel32.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
