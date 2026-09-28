"""K95 vendor HID transport; no input routing or persistent profile writes."""
import ctypes as C
import os
import time
from contextlib import contextmanager
from pathlib import Path

VID, PID = 0x1B1C, 0x1B2D
def packet(*raw):
    if len(raw) > 64:
        raise ValueError('NXP packet exceeds 64 bytes')
    return bytes([0, *raw]) + bytes(64 - len(raw))

class DeviceInfo(C.Structure):
    pass

DeviceInfo._fields_ = [
    ('path', C.c_char_p), ('vendor_id', C.c_ushort), ('product_id', C.c_ushort),
    ('serial_number', C.c_wchar_p), ('release_number', C.c_ushort),
    ('manufacturer_string', C.c_wchar_p), ('product_string', C.c_wchar_p),
    ('usage_page', C.c_ushort), ('usage', C.c_ushort), ('interface_number', C.c_int),
    ('next', C.POINTER(DeviceInfo)),
]

class HidSession:
    """Opens only this keyboard's vendor output and response collections."""
    def __init__(self, dll_path=None):
        if os.name != 'nt':
            raise OSError('The K95 HID backend requires Windows')
        path = Path(dll_path) if dll_path else (
            Path.home() / 'tools/openrgb/OpenRGB Windows 64-bit/hidapi.dll')
        if not path.is_file():
            raise FileNotFoundError('K95 transport library missing: ' + str(path))
        self.dll = C.CDLL(str(path))
        signatures = {
            'hid_enumerate': ([C.c_ushort, C.c_ushort], C.POINTER(DeviceInfo)),
            'hid_free_enumeration': ([C.POINTER(DeviceInfo)], None),
            'hid_open_path': ([C.c_char_p], C.c_void_p),
            'hid_write': ([C.c_void_p, C.c_void_p, C.c_size_t], C.c_int),
            'hid_read_timeout': ([C.c_void_p, C.c_void_p, C.c_size_t, C.c_int], C.c_int),
            'hid_close': ([C.c_void_p], None),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.dll, name)
            fn.argtypes, fn.restype = args, result
        self.output = self.response = None

    def __enter__(self):
        head = self.dll.hid_enumerate(VID, PID)
        endpoints = {3: [], 4: []}
        current = head
        try:
            while current:
                info = current.contents
                if info.usage_page == 0xFFC2 and info.usage in endpoints:
                    endpoints[info.usage].append(info.path)
                current = info.next
        finally:
            self.dll.hid_free_enumeration(head)
        if any(len(paths) != 1 for paths in endpoints.values()):
            raise OSError('Expected one K95 Platinum and its two vendor endpoints; found ' +
                          str({k: len(v) for k, v in endpoints.items()}))
        try:
            self.output = self.dll.hid_open_path(endpoints[4][0])
            self.response = self.dll.hid_open_path(endpoints[3][0])
            if not self.output or not self.response:
                raise OSError('Cannot open K95 vendor endpoints')
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        for name in ('response', 'output'):
            handle = getattr(self, name)
            if handle:
                self.dll.hid_close(handle)
                setattr(self, name, None)

    def write(self, data):
        if len(data) != 65:
            raise ValueError('Expected a 65-byte Windows HID report')
        buf = C.create_string_buffer(data)
        if self.dll.hid_write(self.output, buf, len(data)) != len(data):
            raise OSError('K95 HID write failed')
        time.sleep(0.003)

    def _read(self, timeout_ms):
        buf = C.create_string_buffer(65)
        count = self.dll.hid_read_timeout(self.response, buf, len(buf), timeout_ms)
        if count < 0:
            raise OSError('K95 HID read failed')
        return buf.raw[:count]

    def mode(self):
        for _ in range(32):
            if not self._read(0):
                break
        else:
            raise OSError('K95 response queue busy; another controller may be active')
        self.write(packet(0x0E, 0x00))
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            raw = self._read(100)
            if len(raw) == 65 and raw[0] == 0:
                raw = raw[1:]
            if len(raw) >= 5 and raw[:3] == bytes([0x0E, 0, 0]):
                return raw[4]
        raise TimeoutError('K95 operating-mode query timed out')

@contextmanager
def device_lock():
    """Serialize the monitor and manual tests within this Windows session."""
    kernel = C.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [C.c_void_p, C.c_bool, C.c_wchar_p]
    kernel.CreateMutexW.restype = C.c_void_p
    kernel.WaitForSingleObject.argtypes = [C.c_void_p, C.c_uint]
    kernel.WaitForSingleObject.restype = C.c_uint
    kernel.ReleaseMutex.argtypes = [C.c_void_p]
    kernel.CloseHandle.argtypes = [C.c_void_p]
    handle = kernel.CreateMutexW(None, False, r'Local\K95PlatinumLighting_1B1C_1B2D')
    if not handle:
        raise C.WinError(C.get_last_error())
    acquired = False
    try:
        status = kernel.WaitForSingleObject(handle, 5000)
        if status not in (0, 0x80):
            raise TimeoutError('Another K95 lighting update is still running')
        acquired = True
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)



