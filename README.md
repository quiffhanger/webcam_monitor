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

## Microphone Mute Indicator

Turns the **whole keyboard** (via [OpenRGB](https://openrgb.org/), zone 0 — the key matrix, excluding the light bar) red while your mic is live and unmuted on a call, and off both when muted and when there's no call active. The G1-G6 macro keys are excluded and always stay off (LED indices 113-118 in zone 0, confirmed named `Macro G1`..`Macro G6` via the OpenRGB SDK) — see `G_KEY_LED_INDICES` in `__main__.py`. Requires OpenRGB at `~/tools/openrgb/OpenRGB Windows 64-bit/OpenRGB.exe` (hardcoded in `__main__.py`); logs a warning and continues if not found.

Mute state is detected via the Windows 11 taskbar mic indicator (Explorer's `Shell_TrayWnd` → `SystemTray.AccentButton`, `AutomationId="SystemTrayIcon"`), polled every 0.5s in `mic_taskbar.py` using the `uiautomation` package — not the registry or the audio mixer:

- The `CapabilityAccessManager\ConsentStore\microphone` registry key (used for the webcam lightbar above) only tracks whether an app has an open capture stream — it doesn't change when you mute/unmute mid-call.
- WASAPI's per-app capture session mute (`ISimpleAudioVolume`, the same thing Settings → Sound → Volume mixer exposes) doesn't track it either — confirmed live: Teams' capture session reported `muted=False` via WASAPI while the taskbar showed "Microphone Muted." Teams silences audio in its own pipeline, not at the OS mixer.
- The taskbar mic icon's accessible name (`"Microphone Muted: <App>"` / `"Microphone Unmuted: <App>"`) does track it reliably — verified by polling it side-by-side against Teams' own in-app mute button through several toggles.
- The icon only exists while a call is actively using the mic, so its absence (`mic_taskbar.get_state() == 'none'`) means no call is active — light off.

This used to drive the light bar (zone 1) instead of the keyboard. It moved to zone 0 so the light bar could be dedicated entirely to the calendar timeline below — the two features are on physically separate LEDs and never contend with each other.

## Calendar Meeting Timeline (Light Bar)

The keyboard's light bar (OpenRGB zone 1, 19 individually-addressable LEDs — confirmed via the OpenRGB SDK on a Corsair K95 RGB Platinum) renders a scrolling 5-minute-per-LED timeline of your upcoming meetings, driven by a published Outlook free/busy ICS feed (`config.CALENDAR_ICS_URL`).

- **Layout**: LED index 18 (physically rightmost) covers `[now, now+5m)`; LED index 0 (physically leftmost) covers `[now+90m, now+95m)` — 95 minutes of lookahead across all 19 segments. As time passes, a meeting's lit segments drift from the left toward "now" on the right, purely because the bucket boundaries are recomputed against the clock on every render.
- **BUSY/TENTATIVE/OOF/FREE**: `OOF` and `FREE` are never shown. A `TENTATIVE` hold is dropped if it overlaps a `BUSY` event (the busy one wins) — otherwise it's shown like a normal meeting.
- **Colour**: every meeting gets its own colour from a varied 8-colour palette (see `PRIMARY_COLOURS` in `calendar_lightbar.py`), persisted for as long as it's on the bar (so it doesn't change/flicker as it scrolls), chosen to differ from whichever meetings are adjacent to it in time. Genuinely overlapping (double-booked) meetings get a colour from a second, differently-hued palette (`SECONDARY_COLOURS`), and the segment where they actually overlap renders as the RGB blend of both — a visual "these two clash here" signal.
- **Freshness**: the feed (`.../reachcalendar.ics`, Exchange's internal reachability-calendar link) is regenerated live by Exchange on every request — not the slow-refreshing "publish to internet" feed — so calendar data staleness is just the poll interval (5 minutes by default in `calendar_lightbar.py`).
- **Rendering is event-driven, not polled**: the light bar's rendered state only ever changes at the exact instant some meeting's start/end crosses one of the 19 sliding bucket edges. `calendar_lightbar.seconds_until_next_transition()` computes that exact next instant from the current meeting list, and the render loop in `__main__.py` sleeps precisely until then (waking early if a fresh calendar fetch changes the meeting list first) — so transitions land bang-on-time rather than lagging behind a fixed poll interval.
- **Setup**: add `CALENDAR_ICS_URL` to your local `config.py` (see `config.py.example`). Treat it as a credential — it exposes a year of your busy/free schedule to anyone holding the link — and do not commit the real value anywhere.