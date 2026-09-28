import win32api, winreg, time

key = r'SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam\MSTeams_8wekyb3d8bbwe'
print('Watching Teams webcam key... toggle your camera NOW')
for i in range(3):
    h = win32api.RegOpenKeyEx(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_NOTIFY | winreg.KEY_QUERY_VALUE)
    print(f'  Waiting for change #{i+1}...')
    win32api.RegNotifyChangeKeyValue(h, False, win32api.REG_NOTIFY_CHANGE_LAST_SET, None, False)
    win32api.RegCloseKey(h)
    time.sleep(1)
    reg = winreg.ConnectRegistry(None, winreg.HKEY_CURRENT_USER)
    with winreg.OpenKey(reg, key, 0, winreg.KEY_QUERY_VALUE) as sk:
        stop, _ = winreg.QueryValueEx(sk, 'LastUsedTimeStop')
        status = "OFF" if stop else "ON"
        print(f'  Change detected! LastUsedTimeStop={stop} (camera {status})')
print('Done')
