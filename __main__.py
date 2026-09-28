import asyncio
import ctypes
import os
import signal
import subprocess
import sys
import threading
import tkinter as tk
from tkinter.scrolledtext import ScrolledText
from datetime import datetime, timezone
import logging
import requests
import pystray
from PIL import Image, ImageDraw

OPENRGB_EXE = os.path.join(
    os.path.expanduser('~'), 'tools', 'openrgb', 'OpenRGB Windows 64-bit', 'OpenRGB.exe'
)

# When launched unattended via the Startup shortcut there's no console
# attached, so the default stderr logging handler goes nowhere - a
# persistent file is the only way to see what happened after the fact
# (e.g. diagnosing a stall after the machine sleeps rather than reboots).
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'webcam_monitor.log')
HEARTBEAT_INTERVAL = 300


def _disable_power_throttling():
    """Windows applies EcoQoS power throttling to background processes with
    no visible/focused window - confirmed via the log: heartbeats run fine
    for a while, then just stop for days with no exception and no crash,
    while the process stays visible in Task Manager throughout. Opting out
    via SetProcessInformation is the documented mitigation for exactly this
    "hidden background app goes quiet" behaviour."""
    try:
        PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 0x1
        PROCESS_POWER_THROTTLING_CURRENT_VERSION = 1
        ProcessPowerThrottling = 4

        class PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
            _fields_ = [
                ('Version', ctypes.c_ulong),
                ('ControlMask', ctypes.c_ulong),
                ('StateMask', ctypes.c_ulong),
            ]

        state = PROCESS_POWER_THROTTLING_STATE(
            PROCESS_POWER_THROTTLING_CURRENT_VERSION,
            PROCESS_POWER_THROTTLING_EXECUTION_SPEED,
            0,  # StateMask=0 for the execution-speed bit means "throttling disabled"
        )
        kernel32 = ctypes.windll.kernel32
        # ctypes defaults return types to 32-bit int; GetCurrentProcess's
        # pseudo-handle is pointer-sized, and letting it get truncated on
        # 64-bit here is exactly what caused ERROR_INVALID_HANDLE (6) on
        # the subsequent call - both signatures need to be explicit.
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.SetProcessInformation.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong
        ]
        kernel32.SetProcessInformation.restype = ctypes.c_int
        handle = kernel32.GetCurrentProcess()
        ok = kernel32.SetProcessInformation(
            handle, ProcessPowerThrottling, ctypes.byref(state), ctypes.sizeof(state)
        )
        if ok:
            logging.info('Disabled Windows power throttling for this process')
        else:
            logging.warning(
                f'SetProcessInformation failed (GetLastError={ctypes.GetLastError()}) '
                '- power throttling may still apply'
            )
    except Exception:
        logging.exception('Failed to disable power throttling')

from . import webcam
from . import mic_taskbar
from . import calendar_lightbar
from . import config
from . import diagnostics
from .k95_gkeys import KeyboardService

keyboard_service = KeyboardService(OPENRGB_EXE)

#############################################
# Console Window with stdout redirection
#############################################

class ConsoleWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Console Output")
        self.geometry("1024x768")
        # Create the ScrolledText widget and set it to read-only.
        self.text_area = ScrolledText(self, state='disabled')
        self.text_area.pack(fill=tk.BOTH, expand=True)
        # Instead of destroying, just hide the window so the application can continue.
        self.protocol("WM_DELETE_WINDOW", self.hide)
    
    def write(self, message):
        # Append the message in a thread-safe way.
        self.text_area.configure(state='normal')
        self.text_area.insert(tk.END, message)
        self.text_area.see(tk.END)
        self.text_area.configure(state='disabled')
    
    def hide(self):
        self.withdraw()

# Redirect sys.stdout so that anything printed goes to our text area.
class StdoutRedirector:
    def __init__(self, console):
        self.console = console
        self.original_stdout = sys.stdout

    def write(self, message):
        # Write to the Tkinter window (on the main thread) and to the original stdout.
        self.console.write(message)
        self.original_stdout.write(message)

    def flush(self):
        self.original_stdout.flush()

# Custom logging handler to output log messages to the Tkinter text widget.
class TkinterHandler(logging.Handler):
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget

    def emit(self, record):
        msg = self.format(record) + "\n"
        # Use the widget's 'after' method to schedule thread-safe GUI updates.
        try:
            self.text_widget.after(0, self.append, msg)
        except (RuntimeError, tk.TclError):
            pass  # File logging must keep working during startup/shutdown.

    def append(self, msg):
        self.text_widget.configure(state='normal')
        self.text_widget.insert(tk.END, msg)
        self.text_widget.configure(state='disabled')
        self.text_widget.yview(tk.END)

# Create the console window and override stdout.
console = ConsoleWindow()
sys.stdout = StdoutRedirector(console)
console.withdraw()  # start with the console hidden

#############################################
# Tray Icon Setup (using pystray)
#############################################

def create_image():
    """
    Create an icon image for the system tray.
    Here we create a simple 64x64 blue square with a white circle.
    """
    width, height = 64, 64
    image = Image.new('RGB', (width, height), "blue")
    draw = ImageDraw.Draw(image)
    radius = 20
    center = (width // 2, height // 2)
    draw.ellipse(
        (center[0]-radius, center[1]-radius, center[0]+radius, center[1]+radius),
        fill="white"
    )
    return image

def toggle_console(icon, item):
    """
    Toggle the visibility of the console window.
    (Schedule the call on the Tkinter main loop because Tkinter is not thread-safe.)
    """
    console.after(0, lambda: console.deiconify() if console.state() == 'withdrawn' else console.withdraw())

def quit_app(icon=None, item=None):
    """
    Quit the application cleanly.
    """
    diagnostics.log.info('Application quit requested; tray=%s', icon is not None)
    if icon:
        icon.stop()
    console.after(0, console.destroy)

def setup_tray_icon():
    """
    Set up the tray icon with a menu.
    """
    image = create_image()
    menu = pystray.Menu(
        pystray.MenuItem("Show/Hide Console", toggle_console),
        pystray.MenuItem("Quit", quit_app)
    )
    icon = pystray.Icon("WebcamMonitor", image, "Webcam Monitor", menu)
    return icon

def run_tray_icon():
    """
    Run the system tray icon.
    This call blocks until the icon is stopped.
    """
    icon = setup_tray_icon()
    icon.run()

#############################################
# Async Webcam Monitor & Webhook Caller
#############################################

async def process_webcam_changes():
    async for webcam_key, key_name, on in webcam.watch_queue('webcam'):
        if on:
            logging.info(f"Webcam in use by {key_name} (key: {webcam_key})")
            asyncio.create_task(asyncio.to_thread(call_webhook, config.WEBCAM_ON, {"webcam_key": webcam_key, "key_name": key_name, "status": "on"}))
        else:
            logging.info(f"Webcam no longer in use by {key_name} (key: {webcam_key})")
            asyncio.create_task(asyncio.to_thread(call_webhook, config.WEBCAM_OFF, {"webcam_key": webcam_key, "key_name": key_name, "status": "off"}))


def call_webhook(url, data):
    try:
        response = requests.post(url, json=data)
        response.raise_for_status()
        logging.debug(f"Webhook called successfully: {response.status_code}")
    except requests.RequestException as e:
        logging.error(f"Error calling webhook: {e}")


#############################################
# Microphone Monitor (Keyboard) & Calendar (Light Bar)
#############################################
#
# The keyboard has two independently-addressable OpenRGB zones (confirmed via
# the OpenRGB SDK): zone 0 "Keyboard" (120 LEDs, the whole key matrix) and
# zone 1 "Light Bar" (19 LEDs), contiguous in that order in the device's full
# 139-LED buffer. Splitting the two indicators across the two zones means
# they never contend for the same LEDs.
#
# IMPORTANT: each `OpenRGB.exe --noautoconnect` invocation is a fresh,
# stateless connection that pushes the device's ENTIRE LED buffer, not just
# whatever zone you pass via --zone - a call that only specifies zone 0's
# colours silently blanks zone 1 (and vice versa), since that process has no
# memory of the other zone's current state. So both zones must always be
# sent together in the same call. We keep both zones' current colours in
# memory and re-push the full 139-colour buffer any time either changes.

KEYBOARD_LED_COUNT = 120
LIGHTBAR_LED_COUNT = 19

# G1-G6 (left side macro keys), confirmed via OpenRGB SDK LED names -
# zone 0 LED indices 113-118 are literally named 'Macro G1'..'Macro G6'.
# Excluded from the mic-mute indicator so they stay off/unaffected.
G_KEY_LED_INDICES = set(range(113, 119))

_device_lock = asyncio.Lock()
_device_state = {
    'keyboard': ['000000'] * KEYBOARD_LED_COUNT,
    'lightbar': ['000000'] * LIGHTBAR_LED_COUNT,
}


def _push_device_colors():
    colors = _device_state['keyboard'] + _device_state['lightbar']
    keyboard_service.push_colors(colors)


def _rgb_to_hex(rgb):
    return '%02X%02X%02X' % rgb


async def set_keyboard_color(color_hex: str):
    """Set all keyboard LEDs to a single colour, except the G-keys (left
    side macro keys) and the light bar - both excluded, left off/unaffected."""
    async with _device_lock:
        _device_state['keyboard'] = [
            '000000' if i in G_KEY_LED_INDICES else color_hex
            for i in range(KEYBOARD_LED_COUNT)
        ]
        await asyncio.to_thread(_push_device_colors)


async def set_lightbar_leds(leds_rgb):
    """Set the light bar LEDs from a list of (r, g, b) tuples."""
    async with _device_lock:
        _device_state['lightbar'] = [_rgb_to_hex(c) for c in leds_rgb]
        await asyncio.to_thread(_push_device_colors)


def _keyboard_color_for(mic_state: str) -> str:
    """Red while live and unmuted, off when muted or when nothing's listening
    (no taskbar mic icon means no active call)."""
    return 'FF0000' if mic_state == 'unmuted' else '000000'


async def process_microphone_changes():
    async for mic_state in mic_taskbar.watch():
        logging.info(f"Mic taskbar state: {mic_state}")
        asyncio.create_task(set_keyboard_color(_keyboard_color_for(mic_state)))


async def process_calendar_changes():
    """Renders the 19-segment scrolling meeting timeline onto the light bar.

    The meeting list only updates when calendar_lightbar.watch() yields a
    change (network poll, independent of rendering). The render loop is
    event-driven rather than polled on a fixed interval: after every render
    it computes the exact next instant some meeting's start/end will cross
    one of the 19 sliding bucket edges (calendar_lightbar.
    seconds_until_next_transition) and sleeps exactly until then, so
    transitions land bang-on-time instead of lagging by up to a poll
    interval. That wait is interrupted early if a fresh meeting list arrives
    in the meantime.
    """
    latest_meetings = []
    meetings_updated = asyncio.Event()

    async def _watch_calendar():
        nonlocal latest_meetings
        async for meetings in calendar_lightbar.watch():
            latest_meetings = meetings
            logging.info(f"Calendar meetings visible: {len(meetings)}")
            meetings_updated.set()

    async def _render_loop():
        while True:
            now = datetime.now(timezone.utc)
            leds = calendar_lightbar.compute_leds(latest_meetings, now)
            await set_lightbar_leds(leds)
            wait_seconds = calendar_lightbar.seconds_until_next_transition(latest_meetings, now)
            meetings_updated.clear()
            try:
                await asyncio.wait_for(meetings_updated.wait(), timeout=wait_seconds)
            except asyncio.TimeoutError:
                pass  # exact transition instant reached - loop and re-render

    watch_task = asyncio.create_task(_watch_calendar())
    render_task = asyncio.create_task(_render_loop())
    try:
        await asyncio.gather(watch_task, render_task)
    finally:
        watch_task.cancel()
        render_task.cancel()


async def _heartbeat():
    """Periodic 'still alive' log line so a stalled/throttled process shows
    up as a gap in the log rather than being indistinguishable from a
    healthy quiet period (e.g. no mic/calendar changes for a while)."""
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        logging.info("heartbeat: still running")


async def _run_all_monitors():
    asyncio.get_running_loop().set_exception_handler(diagnostics.loop_exception)
    # Set correct initial keyboard state before entering event loops
    initial = await asyncio.to_thread(mic_taskbar.safe_get_state) or 'none'
    logging.info(f"Startup mic taskbar state: {initial}")
    await set_keyboard_color(_keyboard_color_for(initial))
    await asyncio.gather(
        process_webcam_changes(),
        process_microphone_changes(),
        process_calendar_changes(),
        _heartbeat(),
    )


def run_webcam_monitor():
    diagnostics.log.info('Monitor thread starting; pid=%s', os.getpid())
    try:
        keyboard_service.start()
        asyncio.run(_run_all_monitors())
    except BaseException:
        diagnostics.log.exception('Monitor thread failed; requesting hardware recovery')
        raise
    finally:
        diagnostics.log.info('Monitor thread exiting; requesting hardware recovery')
        try:
            keyboard_service.close()
        except BaseException:
            diagnostics.log.exception('Monitor hardware recovery raised an exception')
            raise
        finally:
            diagnostics.log.info('Monitor thread cleanup finished')

#############################################
# Main Entry Point
#############################################

if __name__ == '__main__':
    diagnostics.install(console)
    diagnostics.log.info('Application starting; pid=%s', os.getpid())
    # Default handler writes to the command window (visible when run
    # manually); the file handler is what actually matters when launched
    # unattended via the Startup shortcut, where there's no console at all.
    file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    logging.basicConfig(level=config.LOG_LEVEL, handlers=[logging.StreamHandler(), file_handler])
    _disable_power_throttling()

    # Add our custom Tkinter logging handler so messages are also sent to the GUI console.
    tk_handler = TkinterHandler(console.text_area)
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    tk_handler.setFormatter(formatter)
    tk_handler.setLevel(logging.DEBUG)
    logging.getLogger().addHandler(tk_handler)

    # Handle Ctrl+C gracefully
    signal.signal(signal.SIGINT, lambda *_: quit_app())
    signal.signal(signal.SIGTERM, lambda *_: quit_app())
    
    # Start the asyncio webcam monitor in a background thread.
    async_thread = threading.Thread(target=run_webcam_monitor, daemon=True)
    async_thread.start()

    # Start the tray icon in a background thread.
    tray_thread = threading.Thread(target=run_tray_icon, daemon=True)
    tray_thread.start()

    # Start the Tkinter mainloop (this call is blocking).
    try:
        console.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        diagnostics.log.info('Tk main loop exited; requesting hardware recovery')
        logging.getLogger().removeHandler(tk_handler)
        keyboard_service.close()
        logging.info('Webcam monitor shutting down')
