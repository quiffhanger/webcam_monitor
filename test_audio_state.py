import ctypes
import ctypes.wintypes
import comtypes
from comtypes import CLSCTX_ALL, GUID, IUnknown, HRESULT
import ctypes.wintypes
import time

# IAudioStateMonitor IID (from Windows SDK audioclient.h)
IID_IAudioStateMonitor = GUID("{63BD8738-E30D-4C77-BF5C-834E87C657E5}")

# Define a minimal IAudioStateMonitor interface
import comtypes
class IAudioStateMonitor(IUnknown):
    _iid_ = IID_IAudioStateMonitor
    _methods_ = [
        comtypes.STDMETHOD(HRESULT, 'RegisterAudioStateChangeNotification', []),
        comtypes.STDMETHOD(HRESULT, 'UnregisterAudioStateChangeNotification', []),
        comtypes.STDMETHOD(ctypes.c_int, 'GetSoundLevel', []),
    ]

dll = ctypes.WinDLL('Windows.Media.MediaControl.dll')
fn = dll['CreateCaptureAudioStateMonitor']
fn.restype = HRESULT

# Try different arities - the WinRT factory may not take a REFIID
raw_ptr = ctypes.c_void_p()

# Attempt 1: (REFIID, void**)
hr = fn(ctypes.byref(IID_IAudioStateMonitor), ctypes.byref(raw_ptr))
print(f"Attempt 1 (REFIID, void**): hr={hr:#010x} ptr={raw_ptr.value}")

# Attempt 2: (void**) only
raw_ptr2 = ctypes.c_void_p()
try:
    fn2 = dll['CreateCaptureAudioStateMonitor']
    fn2.restype = HRESULT
    fn2.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
    hr2 = fn2(ctypes.byref(raw_ptr2))
    print(f"Attempt 2 (void** only):   hr={hr2:#010x} ptr={raw_ptr2.value}")
except Exception as e:
    print(f"Attempt 2 failed: {e}")

# Use whichever got a non-null pointer
for ptr_val in [raw_ptr.value, raw_ptr2.value]:
    if ptr_val:
        print(f"Got valid pointer: {ptr_val:#x}")
        vtable = ctypes.cast(ptr_val, ctypes.POINTER(ctypes.c_void_p))[0]
        vtable_arr = ctypes.cast(vtable, ctypes.POINTER(ctypes.c_void_p))
        # vtable[5] = GetSoundLevel: 0=Muted, 1=Low, 2=Full
        get_sound_level = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p)(vtable_arr[5])

        print("Press Win+Alt+K to toggle mute - watching for 60s...")
        prev = None
        for _ in range(120):
            level = get_sound_level(ptr_val)
            if level != prev:
                labels = {0: 'MUTED', 1: 'Low', 2: 'Full'}
                print(f"  CHANGED -> SoundLevel: {labels.get(level, level)} ({level})")
                prev = level
            time.sleep(0.5)
        break
else:
    print("All attempts returned null pointer.")
