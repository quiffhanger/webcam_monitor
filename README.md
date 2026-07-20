# webcam_monitor
Simple module to watch for devices using your webcam (windows only) and run a webhook if any process turns your camera on or off. Use the registry keys in SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam which windows modifies when a webcam is used.

Runs with tray icon which can be used to open a console to see logs.

Useful to:
1. Turn on lighting to improve camera image
2. Turn on a warning light to notify you/others that you're on camera

## Setup

1. **Clone the repository**:
    ```sh
    git clone https://github.com/yourusername/webcam_monitor.git
    cd webcam_monitor
    ```

2. **Create a virtual environment**:
    ### For Windows:
    ```sh
    python -m venv .venv
    .venv\Scripts\activate
    ```
3. **Install dependencies**:
    ```sh
    pip install -r requirements.txt
    ```

4. **Run the application**:
    ```sh
    python -m webcam_monitor
    ```

## Configuration

Copy [config.py.example](http://_vscodecontentref_/3) to [config.py](http://_vscodecontentref_/4) and update the configuration as needed.

```sh
cp config.py.example config.py

## Launch in background at startup

Stat, run, type shell:startup
Create a new shortcut

powershell.exe -ExecutionPolicy Bypass -File "C:\path_to\webcam_monitor\launch.ps1"
Modify properties of shortcut to "Start in" "C:\path_to" - e.g directory webcam_monitor is in 

This hides the powershell window. To kill it go via the taskbar icon.

NOTE: will only monitor apps that have been installed/granted webcam access before it starts up. Todo - monitor for additional keys that are create after is starts up.

## Microphone Mute Lightbar

Turns the keyboard lightbar (via [OpenRGB](https://openrgb.org/)) red while your mic is live and unmuted on a call, and off both when muted and when there's no call active. Requires OpenRGB at `~/tools/openrgb/OpenRGB Windows 64-bit/OpenRGB.exe` (hardcoded in `__main__.py`); logs a warning and continues if not found.

Mute state is detected via the Windows 11 taskbar mic indicator (Explorer's `Shell_TrayWnd` → `SystemTray.AccentButton`, `AutomationId="SystemTrayIcon"`), polled every 0.5s in `mic_taskbar.py` using the `uiautomation` package — not the registry or the audio mixer:

- The `CapabilityAccessManager\ConsentStore\microphone` registry key (used for the webcam lightbar above) only tracks whether an app has an open capture stream — it doesn't change when you mute/unmute mid-call.
- WASAPI's per-app capture session mute (`ISimpleAudioVolume`, the same thing Settings → Sound → Volume mixer exposes) doesn't track it either — confirmed live: Teams' capture session reported `muted=False` via WASAPI while the taskbar showed "Microphone Muted." Teams silences audio in its own pipeline, not at the OS mixer.
- The taskbar mic icon's accessible name (`"Microphone Muted: <App>"` / `"Microphone Unmuted: <App>"`) does track it reliably — verified by polling it side-by-side against Teams' own in-app mute button through several toggles.
- The icon only exists while a call is actively using the mic, so its absence (`mic_taskbar.get_state() == 'none'`) means no call is active — light off.