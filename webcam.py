import win32api
import win32con
import logging
import winreg
import asyncio
import time

hiveToWatch = winreg.HKEY_CURRENT_USER
keysToWatch = r'SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam\NonPackaged'
value_name = "LastUsedTimeStop"


async def watch_webcam(webcam_key):
    return await asyncio.to_thread(watch_key, webcam_key)


def watch_key(key_path):
    logging.info(f'Monitoring {key_path}')
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
    with winreg.OpenKey(reg, keysToWatch, 0, winreg.KEY_READ) as k:
        for i in range(0, 999):
            try:
                yield '\\'.join((keysToWatch, winreg.EnumKey(k, i)))
            except OSError:
                return

def simple_watch_test():
    hiveToWatch = win32con.HKEY_CURRENT_USER
    keyToWatch = r'SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam\NonPackaged\C:#Program Files (x86)#Zoom#bin#Zoom.exe'
    handleToBeWatched = win32api.RegOpenKeyEx(hiveToWatch, keyToWatch, 0, win32con.KEY_NOTIFY)
    win32api.RegNotifyChangeKeyValue(handleToBeWatched, False, win32api.REG_NOTIFY_CHANGE_LAST_SET, None, False)
    win32api.RegCloseKey(handleToBeWatched)

async def webcam_state_change(webcam_key):
    while True:
        key_name, on = await watch_webcam(webcam_key)
        if on:
            logging.info(f'Webcam in use by {key_name}')
        else:
            logging.info(f'Webcam no longer in use by {key_name}')

async def main():
    await asyncio.gather(
        *(webcam_state_change(k) for k in get_keys_to_watch()),
    )

if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    asyncio.run(main())
