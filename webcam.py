import win32api
import win32con
import logging
import winreg
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

hiveToWatch = winreg.HKEY_CURRENT_USER
keyToWatch = r'SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam'
keysToWatch = (keyToWatch,'\\'.join((keyToWatch, 'NonPackaged')))
value_name = "LastUsedTimeStop"


# Dedicated pool sized for all blocking registry watchers + headroom
_watcher_pool = None

def _get_pool(num_keys):
    global _watcher_pool
    if _watcher_pool is None:
        _watcher_pool = ThreadPoolExecutor(max_workers=num_keys + 4)
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
    return key_path, webcam_on(key_path)


def webcam_on(webcam_key):
    reg = winreg.ConnectRegistry(None, winreg.HKEY_CURRENT_USER)
    with winreg.OpenKey(reg, webcam_key, 0, winreg.KEY_QUERY_VALUE) as sk:
        value, value_type = winreg.QueryValueEx(sk, value_name)
        return (value == 0)


def get_keys_to_watch():
    reg = winreg.ConnectRegistry(None, winreg.HKEY_CURRENT_USER)
    for keyToWatch in keysToWatch:
        with winreg.OpenKey(reg, keyToWatch, 0, winreg.KEY_READ) as k:
            for i in range(0, 999):
                try:
                    yield '\\'.join((keyToWatch, winreg.EnumKey(k, i)))
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

def create_webcam_queue():
    """
    Creates an asyncio.Queue and starts background tasks to monitor each webcam key.
    
    Returns:
        asyncio.Queue: A queue into which state change events will be placed.
    """
    event_queue = asyncio.Queue()
    keys = list(get_keys_to_watch())
    _get_pool(len(keys))
    logging.info(f'Monitoring {len(keys)} webcam registry keys')
    for key in keys:
        logging.info(f'  {key.split(chr(92))[-1]}')
        task = asyncio.create_task(_monitor_key(key, event_queue))
        _monitor_tasks.add(task)
        task.add_done_callback(_monitor_tasks.discard)
    return event_queue

async def watch_queue():
    """
    Asynchronously yields events as they are available in the queue.
    
    Yields:
        tuple: A tuple of (webcam_key, key_name, on) for each state change event.
    """
    queue = create_webcam_queue()
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
