"""Software G1-G6 bridge with an independent, fail-closed hardware guardian.

No onboard writes or input-routing changes. OpenRGB retains its existing setup.
"""
import atexit
import ctypes as C
import logging
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time

try:
    from .k95_transport import HidSession, VID, PID, packet, device_lock
except ImportError:
    from k95_transport import HidSession, VID, PID, packet, device_lock

LOG = logging.getLogger(__name__)


def g_mask(report):
    if len(report) == 65 and report[0] == 0:
        report = report[1:]
    if len(report) != 64 or report[0] != 3:
        return None
    return report[16] & 0x3F


class Edges:
    def __init__(self):
        self.previous = 0

    def update(self, report):
        current = g_mask(report)
        if current is None:
            return []
        pressed = current & ~self.previous
        self.previous = current
        return [i + 1 for i in range(6) if pressed & (1 << i)]


class KeyInput(HidSession):
    def __enter__(self):
        head = self.dll.hid_enumerate(VID, PID)
        paths = []
        item = head
        try:
            while item:
                info = item.contents
                if info.usage_page == 0xFFC0 and info.usage == 2:
                    paths.append(info.path)
                item = info.next
        finally:
            self.dll.hid_free_enumeration(head)
        if len(paths) != 1:
            raise OSError('Expected exactly one K95 vendor G-key input')
        self.response = self.dll.hid_open_path(paths[0])
        if not self.response:
            raise OSError('Cannot open K95 G-key input')
        # Discard stale buffered presses before lighting is allowed to start.
        for _ in range(64):
            if not self._read(0):
                break
        return self


class KeyboardInput(C.Structure):
    _fields_ = [('vk', C.c_ushort), ('scan', C.c_ushort), ('flags', C.c_ulong),
                ('time', C.c_ulong), ('extra', C.c_size_t)]


class MouseInput(C.Structure):
    _fields_ = [('dx', C.c_long), ('dy', C.c_long), ('data', C.c_ulong),
                ('flags', C.c_ulong), ('time', C.c_ulong), ('extra', C.c_size_t)]


class InputUnion(C.Union):
    _fields_ = [('ki', KeyboardInput), ('mi', MouseInput)]


class Input(C.Structure):
    _fields_ = [('type', C.c_ulong), ('u', InputUnion)]


def chord_events(number, held):
    if number not in range(1, 7):
        raise ValueError('G-key must be 1-6')
    modifiers = [key for key in (0x11, 0x10, 0x12) if not held(key)]
    digit = 0x30 + number
    events = [(key, 0) for key in modifiers] + [(digit, 0), (digit, 2)]
    events += [(key, 2) for key in reversed(modifiers)]
    return events, [(digit, 2)] + [(key, 2) for key in reversed(modifiers)]


def send_chord(number):
    user = C.WinDLL('user32', use_last_error=True)
    user.GetAsyncKeyState.argtypes = [C.c_int]
    user.GetAsyncKeyState.restype = C.c_short
    user.SendInput.argtypes = [C.c_uint, C.POINTER(Input), C.c_int]
    user.SendInput.restype = C.c_uint
    events, cleanup = chord_events(number, lambda vk: user.GetAsyncKeyState(vk) & 0x8000)

    def send(items):
        values = (Input * len(items))()
        for value, (vk, flags) in zip(values, items):
            value.type = 1
            value.u.ki = KeyboardInput(vk, 0, flags, 0, 0)
        return user.SendInput(len(values), values, C.sizeof(Input))

    if send(events) != len(events):
        send(cleanup)
        raise OSError('Windows rejected the G-key shortcut (possibly elevation mismatch)')


def restore_hardware():
    last = None
    for delay in (0, 0.3, 0.8, 1.5):
        time.sleep(delay)
        try:
            with HidSession() as hid:
                hid.write(packet(7, 4, 1))
                time.sleep(0.03)
                hid.write(packet(7, 4, 1))
                if hid.mode() != 1:
                    raise OSError('Keyboard did not confirm hardware mode')
            LOG.info('K95 hardware mode restored and verified')
            return True
        except Exception as exc:
            last = exc
    LOG.error('K95 hardware recovery failed: %s; unplug/replug may be required', last)
    return False


def lines_to_queue(stream, inbox):
    try:
        for line in stream:
            inbox.put(line.rstrip('\r\n'))
    finally:
        inbox.put(None)


def valid_colors(colors):
    return len(colors) == 139 and all(re.fullmatch('[0-9a-fA-F]{6}', c) for c in colors)


def guardian(parent_pid, executable):
    """Own all lighting children; stop them before restoration on every exit path."""
    kernel = C.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [C.c_ulong, C.c_bool, C.c_ulong]
    kernel.OpenProcess.restype = C.c_void_p
    kernel.WaitForSingleObject.argtypes = [C.c_void_p, C.c_ulong]
    kernel.WaitForSingleObject.restype = C.c_ulong
    kernel.CloseHandle.argtypes = [C.c_void_p]
    parent = kernel.OpenProcess(0x100000, False, parent_pid)
    if not parent:
        raise C.WinError(C.get_last_error())
    # A kill-on-close Windows job prevents orphan OpenRGB writes even if this
    # helper itself crashes. Start children suspended until assigned to it.
    import win32api
    import win32job
    job = win32job.CreateJobObject(None, '')
    limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    limits['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
    ntdll = C.WinDLL('ntdll')
    ntdll.NtResumeProcess.argtypes = [C.c_void_p]
    ntdll.NtResumeProcess.restype = C.c_long
    inbox = queue.Queue()
    threading.Thread(target=lines_to_queue, args=(sys.stdin, inbox), daemon=True).start()
    child = None
    try:
        with device_lock():
            try:
                # Establish a known hardware baseline before advertising readiness.
                if not restore_hardware():
                    return 2
                print('READY ' + str(os.getpid()), flush=True)
                LOG.info('Guardian ready; owner pid=%s, guardian pid=%s', parent_pid, os.getpid())
                lease = time.monotonic()
                started = 0
                while True:
                    parent_status = kernel.WaitForSingleObject(parent, 0)
                    if parent_status != 0x102:
                        LOG.info('Recovery reason: owner process ended or wait failed; status=%s', parent_status)
                        break
                    now = time.monotonic()
                    if now - lease > 8:
                        LOG.error('K95 owner heartbeat expired')
                        break
                    if child is not None:
                        code = child.poll()
                        if code is not None:
                            child = None
                            if code:
                                raise OSError('OpenRGB failed: exit ' + str(code))
                            print('OK', flush=True)
                        elif now - started > 12:
                            raise TimeoutError('OpenRGB update timed out')
                    try:
                        command = inbox.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    if command is None or command == 'STOP':
                        LOG.info('Recovery reason: %s', 'owner command pipe closed' if command is None else 'owner requested STOP')
                        break
                    if command == 'PING':
                        lease = now
                    elif command.startswith('COLOR '):
                        colors = command[6:].split(',')
                        if child is not None or not valid_colors(colors):
                            raise ValueError('Invalid or overlapping RGB update')
                        with open(Path(__file__).with_name('k95_openrgb.log'), 'wb') as output:
                            child = subprocess.Popen(
                                [executable, '--noautoconnect', '--device', '0', '--color', ','.join(colors)],
                                stdout=output, stderr=output,
                                creationflags=subprocess.CREATE_NO_WINDOW | 0x4)
                        win32job.AssignProcessToJobObject(job, int(child._handle))
                        if ntdll.NtResumeProcess(int(child._handle)) != 0:
                            raise OSError('Could not resume managed OpenRGB update')
                        started = now
                    else:
                        raise ValueError('Unknown guardian command')
            except BaseException:
                LOG.exception('Recovery reason: guardian operation failed')
                raise
            finally:
                try:
                    if child is not None and child.poll() is None:
                        child.kill()
                        child.wait(timeout=3)
                finally:
                    win32job.TerminateJobObject(job, 1)
                    if not restore_hardware():
                        raise OSError('Hardware mode recovery could not be verified')
    finally:
        win32api.CloseHandle(job)
        kernel.CloseHandle(parent)
    return 0


class KeyboardService:
    def __init__(self, executable):
        self.executable = str(executable)
        self.stopped = threading.Event()
        self.write_lock = threading.Lock()
        self.update_lock = threading.Lock()
        self.close_lock = threading.Lock()
        self.replies = queue.Queue()
        self.guard = None
        self.listener = None
        self.input = None
        self.ready = False
        self.armed = False

    def _command(self, value):
        with self.write_lock:
            self.guard.stdin.write(value + '\n')
            self.guard.stdin.flush()

    def start(self):
        try:
            self.input = KeyInput().__enter__()
            self.guard = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), '--guard', str(os.getpid()), self.executable],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1, creationflags=subprocess.CREATE_NO_WINDOW)
            threading.Thread(target=lines_to_queue, args=(self.guard.stdout, self.replies), daemon=True).start()
            greeting = self.replies.get(timeout=15)
            if not greeting or not greeting.startswith('READY '):
                raise OSError('Hardware recovery helper did not become ready')
            self.guardian_pid = int(greeting.split()[1])
            self.armed = True
            self.listener = threading.Thread(target=self._listen, name='K95 G-keys', daemon=True)
            self.listener.start()
            self.ready = True
            threading.Thread(target=self._heartbeat, name='K95 guardian heartbeat', daemon=True).start()
            atexit.register(self.close)
            LOG.info('K95 G1-G6 software bridge and crash recovery ready')
            return True
        except Exception:
            LOG.exception('K95 bridge unavailable; lighting disabled to preserve hardware macros')
            self.close()
            return False

    def _listen(self):
        edges = Edges()
        try:
            while not self.stopped.is_set():
                report = self.input._read(100)
                if report:
                    for number in edges.update(report):
                        if self.stopped.is_set():
                            return
                        send_chord(number)
                        LOG.info('K95 G%s shortcut sent', number)
        except Exception:
            LOG.exception('K95 input failed; restoring hardware mode')
            self.close()
        finally:
            self.input.__exit__()

    def _heartbeat(self):
        try:
            while not self.stopped.wait(1):
                if self.guard.poll() is not None:
                    raise OSError('K95 recovery helper exited')
                self._command('PING')
        except Exception:
            LOG.exception('K95 recovery helper lost; disabling lighting')
            self.close()

    def push_colors(self, colors):
        if not valid_colors(colors):
            raise ValueError('Expected 139 six-digit RGB colors')
        with self.update_lock:
            if not self.ready or self.stopped.is_set():
                return
            try:
                self._command('COLOR ' + ','.join(colors))
                if self.replies.get(timeout=15) != 'OK':
                    raise OSError('K95 lighting helper stopped')
            except Exception:
                LOG.exception('K95 lighting failed; restoring hardware mode')
                self.close()

    def close(self):
        # First block new updates/injections. Do not wait on the update lock:
        # the guardian can cancel its own in-flight lighting child safely.
        self.stopped.set()
        self.ready = False
        if not self.close_lock.acquire(blocking=False):
            return
        try:
            if self.guard is not None:
                try:
                    self._command('STOP')
                except (OSError, ValueError):
                    pass
                try:
                    code = self.guard.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    LOG.error('K95 recovery is still running; helper left alive to finish')
                    return
                if code and self.armed:
                    # The helper is dead: take its mutex before fallback recovery.
                    with device_lock():
                        restore_hardware()
                try:
                    self.guard.stdin.close()
                except OSError:
                    pass
                self.guard = None
            if self.listener is None and self.input is not None:
                self.input.__exit__()
                self.input = None
        except Exception:
            LOG.exception('K95 shutdown recovery failed')
        finally:
            self.close_lock.release()


if __name__ == '__main__':
    logging.basicConfig(filename=str(Path(__file__).with_name('k95_recovery.log')),
                        level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    if len(sys.argv) == 4 and sys.argv[1] == '--guard':
        try:
            sys.exit(guardian(int(sys.argv[2]), sys.argv[3]))
        except Exception:
            LOG.exception('K95 guardian failed')
            sys.exit(1)
    raise SystemExit('This module is managed by webcam_monitor, not launched directly.')
