import win32api
import win32con
import logging
import winreg
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

hiveToWatch = winreg.HKEY_CURRENT_USER
_BASE_KEY = r'SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore'
keyToWatch = '\\'.join((_BASE_KEY, 'webcam'))  # kept for backwards compat
keysToWatch = (keyToWatch, '\\'.join((keyToWatch, 'NonPackaged')))  # kept for backwards compat
value_name = "LastUsedTimeStop"


# Dedicated pool sized for all blocking registry watchers + headroom
_watcher_pool = None

def _get_pool(num_keys=0):
    global _watcher_pool
    if _watcher_pool is None:
        # Size generously upfront to cover all device types without needing resize
        _watcher_pool = ThreadPoolExecutor(max_workers=max(num_keys + 4, 32))
    return _watcher_pool

async def watch_webcam(webcam_key):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_watcher_pool, watch_key, webcam_key)


def watch_key(key_path):
    logging.debug(f'Waiting for change on {key_path}')
    handleToBeWatched = win32api.RegOpenKeyEx(hiveToWatch, key_path, 0, winreg.KEY_NOTIFY|winreg.KEY_QUERY_VALUE)
    win32api.RegNotifyChangeKeyValue(handleToBeWatched, False, win32api.REG_NOTIFY_CHANGE_LAST_SET, None, False)
    win32api.RegCloseKey(handleToBeWatched)
    time.sleep(1.5)  # close handle and wait before returning as change takes a while to actually stick
    return key_path, device_in_use(key_path)


def device_in_use(key_path):
    """Returns True if LastUsedTimeStop == 0 (device actively in use). False if not in use or value missing."""
    try:
        reg = winreg.ConnectRegistry(None, winreg.HKEY_CURRENT_USER)
        with winreg.OpenKey(reg, key_path, 0, winreg.KEY_QUERY_VALUE) as sk:
            value, _ = winreg.QueryValueEx(sk, value_name)
            return (value == 0)
    except OSError:
        return False

def webcam_on(webcam_key):  # backwards compat alias
    return device_in_use(webcam_key)


def get_keys_to_watch(device_name='webcam'):
    base = '\\'.join((_BASE_KEY, device_name))
    roots = (base, '\\'.join((base, 'NonPackaged')))
    reg = winreg.ConnectRegistry(None, winreg.HKEY_CURRENT_USER)
    for root in roots:
        with winreg.OpenKey(reg, root, 0, winreg.KEY_READ) as k:
            for i in range(0, 999):
                try:
                    sub = winreg.EnumKey(k, i)
                    if sub == 'NonPackaged':  # container key, no LastUsedTimeStop
                        continue
                    yield '\\'.join((root, sub))
                except OSError:
                    break


async def _monitor_key(webcam_key, event_queue):
    """
    Monitors a single webcam key indefinitely. Each time a state change is detected,
    a tuple is put into the event_queue. Logs and retries on errors to avoid silent death.
    """
    key_short = webcam_key.split('\\')[-1]
    while True:
        try:
            key_name, on = await watch_webcam(webcam_key)
            await event_queue.put((webcam_key, key_name, on))
        except Exception:
            logging.exception(f'Error monitoring {key_short}, retrying in 5s')
            await asyncio.sleep(5)


# Strong references to monitor tasks so they aren't garbage collected
_monitor_tasks = set()

def create_device_queue(device_name='webcam'):
    """
    Creates an asyncio.Queue and starts background tasks to monitor each registry key
    for the given CapabilityAccessManager device (e.g. 'webcam', 'microphone').
    """
    event_queue = asyncio.Queue()
    keys = list(get_keys_to_watch(device_name))
    _get_pool(len(keys))
    logging.info(f'Monitoring {len(keys)} {device_name} registry keys')
    for key in keys:
        logging.info(f'  {key.split(chr(92))[-1]}')
        task = asyncio.create_task(_monitor_key(key, event_queue))
        _monitor_tasks.add(task)
        task.add_done_callback(_monitor_tasks.discard)
    return event_queue

def create_webcam_queue():  # backwards compat alias
    return create_device_queue('webcam')

async def watch_queue(device_name='webcam'):
    """
    Asynchronously yields (key, key_name, on) tuples as device state changes.
    """
    queue = create_device_queue(device_name)
    while True:
        event = await queue.get()
        yield event

if __name__ == '__main__':
    import signal
    logging.basicConfig(level=logging.DEBUG)

    async def _main():
        async for webcam_key, key_name, on in watch_queue():
            status = 'ON' if on else 'OFF'
            logging.info(f"[{status}] {key_name.split(chr(92))[-1]}")

    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, loop.stop)
    try:
        loop.run_until_complete(_main())
    finally:
        loop.close()
