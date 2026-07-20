"""Detects live mic mute state via the Windows 11 taskbar mic indicator
(Explorer's SystemTray.AccentButton, AutomationId "SystemTrayIcon"). That
icon only exists while a call is actively using the microphone, so its
absence means no call is active — nothing listening.
"""
import asyncio
import logging

import uiautomation as auto

POLL_INTERVAL = 0.5


def get_state() -> str:
    """Returns 'muted', 'unmuted', or 'none' (no taskbar mic icon - no active call).

    Runs its own COM apartment init/teardown (auto.UIAutomationInitializerInThread)
    since this is called via asyncio.to_thread on arbitrary pool threads that never
    call CoInitialize on their own.
    """
    with auto.UIAutomationInitializerInThread():
        tray = auto.PaneControl(ClassName='Shell_TrayWnd', searchDepth=1)
        if not tray.Exists(0, 0):
            return 'none'
        for control, _ in auto.WalkControl(tray, includeTop=False, maxDepth=20):
            try:
                if control.AutomationId != 'SystemTrayIcon':
                    continue
                name = control.Name or ''
                first_line = name.split('\n', 1)[0]
                if first_line.startswith('Microphone Unmuted'):
                    return 'unmuted'
                if first_line.startswith('Microphone Muted'):
                    return 'muted'
            except Exception:
                continue
    return 'none'


async def watch(poll_interval: float = POLL_INTERVAL):
    """Yields the mic taskbar state ('muted' / 'unmuted' / 'none') each time it changes."""
    last = None
    while True:
        state = await asyncio.to_thread(get_state)
        if state != last:
            logging.debug(f'Mic taskbar state -> {state}')
            yield state
            last = state
        await asyncio.sleep(poll_interval)
