"""
Focus Mode v2 - local desktop app (Windows)

- Pick allowed apps + up to 3 websites + a duration (minutes).
- Once started, it CANNOT be stopped. It ends only when the timer ends.
- Everything not allowed gets closed (Task Manager, Settings, terminals too).
- Chrome / Edge / Brave are locked with browser policies: only your 3 sites open,
  all other URLs and searches are blocked, private/incognito mode is disabled.
- Other browsers (Firefox, Opera, Vivaldi) are limited through a local proxy.
- If the app is killed or the PC restarts, running it again resumes the session.

Setup:   pip install psutil
Run:     double-click focus_mode.pyw
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import select
import socket
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import messagebox, ttk
from urllib.parse import urlparse

import psutil
import winreg

PROXY_PORT = 8899
MAX_SITES = 3
STATE_FILE = os.path.join(os.path.expanduser("~"), ".focus_mode_state.json")
LOG_FILE = os.path.join(os.path.expanduser("~"), "focus_mode_log.txt")


def log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass


POLICY_BROWSERS = {  # exe -> (policy registry path, private-mode policy name)
    "msedge.exe": (r"Software\Policies\Microsoft\Edge", "InPrivateModeAvailability"),
    "chrome.exe": (r"Software\Policies\Google\Chrome", "IncognitoModeAvailability"),
    "brave.exe": (r"Software\Policies\BraveSoftware\Brave", "IncognitoModeAvailability"),
}
PROXY_BROWSERS = {"firefox.exe", "opera.exe", "vivaldi.exe"}
BROWSERS = set(POLICY_BROWSERS) | PROXY_BROWSERS

# Only the bare Windows shell. Task Manager, Settings, terminals are NOT here,
# so they get closed during focus.
SYSTEM_SAFE = {
    "explorer.exe", "dwm.exe", "applicationframehost.exe", "textinputhost.exe",
    "searchhost.exe", "searchapp.exe", "startmenuexperiencehost.exe",
    "shellexperiencehost.exe", "lockapp.exe", "csrss.exe", "winlogon.exe",
    "ctfmon.exe", "sihost.exe", "python.exe", "pythonw.exe", "py.exe",
}

user32 = ctypes.windll.user32
WM_CLOSE = 0x0010
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)


# ----------------------------------------------------------------- windows
def _each_window(fn):
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindowTextLengthW(hwnd) == 0:
            return True
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            name = psutil.Process(pid.value).name().lower()
        except Exception:
            return True
        fn(hwnd, pid.value, name)
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)


def visible_app_names():
    names = set()
    _each_window(lambda h, p, n: names.add(n))
    return names


def close_disallowed(keep, protected_pids):
    def fn(hwnd, pid, name):
        if pid not in protected_pids and name not in keep:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)

    _each_window(fn)


# --------------------------------------------------------- browser policies
def _delete_policy(path, private_name):
    for sub in ("URLBlocklist", "URLAllowlist"):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path + "\\" + sub)
        except OSError:
            pass
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE)
        try:
            winreg.DeleteValue(k, private_name)
        except OSError:
            pass
        winreg.CloseKey(k)
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)  # only works if empty
    except OSError:
        pass


def apply_browser_policies(sites):
    allow = list(sites) + ["about:blank"]
    for exe, (path, private_name) in POLICY_BROWSERS.items():
        _delete_policy(path, private_name)
        k = winreg.CreateKey(winreg.HKEY_CURRENT_USER, path)
        winreg.SetValueEx(k, private_name, 0, winreg.REG_DWORD, 1)  # disable private mode
        winreg.CloseKey(k)
        b = winreg.CreateKey(winreg.HKEY_CURRENT_USER, path + r"\URLBlocklist")
        winreg.SetValueEx(b, "1", 0, winreg.REG_SZ, "*")  # block everything...
        winreg.CloseKey(b)
        a = winreg.CreateKey(winreg.HKEY_CURRENT_USER, path + r"\URLAllowlist")
        for i, s in enumerate(allow, 1):  # ...except these
            winreg.SetValueEx(a, str(i), 0, winreg.REG_SZ, s)
        winreg.CloseKey(a)


def remove_browser_policies():
    for exe, (path, private_name) in POLICY_BROWSERS.items():
        _delete_policy(path, private_name)


# ------------------------------------------------------------------- proxy
class SiteProxy:
    """HTTP/HTTPS proxy that only allows given domains (used for Firefox etc.)."""

    def __init__(self):
        self.allowed = set()
        self.running = False
        self.sock = None

    def ok(self, host):
        host = host.lower()
        return any(host == d or host.endswith("." + d) for d in self.allowed)

    def start(self):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", PROXY_PORT))
        self.sock.listen(100)
        self.running = True
        threading.Thread(target=self._accept, daemon=True).start()

    def stop(self):
        self.running = False
        try:
            self.sock.close()
        except Exception:
            pass

    def _accept(self):
        while self.running:
            try:
                c, _ = self.sock.accept()
                threading.Thread(target=self._handle, args=(c,), daemon=True).start()
            except Exception:
                break

    def _handle(self, c):
        try:
            data = c.recv(65536)
            if not data:
                return
            first = data.split(b"\r\n", 1)[0].decode(errors="ignore").split()
            method, target = first[0], first[1]
            if method == "CONNECT":
                host, _, port = target.partition(":")
                port = int(port or 443)
            else:
                u = urlparse(target)
                host, port = u.hostname or "", u.port or 80
            if not host or not self.ok(host):
                body = b"<h2>Blocked by Focus Mode</h2>"
                c.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/html\r\n"
                          b"Content-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body)
                return
            r = socket.create_connection((host, port), timeout=10)
            if method == "CONNECT":
                c.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            else:
                r.sendall(data)
            socks = [c, r]
            while True:
                rd, _, x = select.select(socks, [], socks, 60)
                if x or not rd:
                    break
                for s in rd:
                    chunk = s.recv(65536)
                    if not chunk:
                        return
                    (r if s is c else c).sendall(chunk)
        except Exception:
            pass
        finally:
            try:
                c.close()
            except Exception:
                pass


def set_system_proxy(enable):
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                         r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
                         0, winreg.KEY_SET_VALUE)
    if enable:
        winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, f"127.0.0.1:{PROXY_PORT}")
        winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, "localhost;127.0.0.1")
    winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1 if enable else 0)
    winreg.CloseKey(key)
    wininet = ctypes.windll.wininet
    wininet.InternetSetOptionW(0, 39, 0, 0)
    wininet.InternetSetOptionW(0, 37, 0, 0)


def clean_domain(text):
    text = text.strip().lower()
    if not text:
        return ""
    if "//" not in text:
        text = "//" + text
    host = urlparse(text).hostname or ""
    return host[4:] if host.startswith("www.") else host


def clear_restrictions():
    remove_browser_policies()
    try:
        set_system_proxy(False)
    except Exception:
        pass


# --------------------------------------------------------------------- app
class FocusApp:
    def __init__(self, root):
        self.root = root
        root.title("Focus Mode")
        root.geometry("380x320")
        root.resizable(False, False)
        self.proxy = SiteProxy()
        self.active = False
        self.allowed_apps = set()
        self.sites = []
        self.end_time = None
        self.protected = {os.getpid()}
        self.protected.update(a.pid for a in psutil.Process(os.getpid()).parents())

        self.status = ttk.Label(root, text="Focus mode is OFF", font=("Segoe UI", 14, "bold"))
        self.status.pack(pady=(18, 6))
        self.timer_lbl = ttk.Label(root, text="", font=("Segoe UI", 12))
        self.timer_lbl.pack()
        self.btn = ttk.Button(root, text="Start Focus Mode", command=self.open_setup)
        self.btn.pack(pady=12, ipadx=12, ipady=6)

        self.sites_frame = ttk.LabelFrame(root, text=f"Allowed websites (max {MAX_SITES})")
        self.site_vars = [tk.StringVar() for _ in range(MAX_SITES)]
        for v in self.site_vars:
            ttk.Entry(self.sites_frame, textvariable=v, width=34).pack(padx=10, pady=3)
        ttk.Button(self.sites_frame, text="Update sites", command=self.update_sites).pack(pady=6)

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.try_resume()

    # --- resume / state
    def try_resume(self):
        try:
            with open(STATE_FILE) as f:
                st = json.load(f)
        except Exception:
            return
        try:
            if st.get("end", 0) > time.time():
                self.begin(set(st["apps"]), st["sites"], st["end"])
            else:
                clear_restrictions()
                self.delete_state()
        except Exception:
            log("resume failed:\n" + traceback.format_exc())
            self.active = False
            clear_restrictions()
            self.delete_state()

    def save_state(self):
        with open(STATE_FILE, "w") as f:
            json.dump({"end": self.end_time, "apps": sorted(self.allowed_apps),
                       "sites": self.sites}, f)

    @staticmethod
    def delete_state():
        try:
            os.remove(STATE_FILE)
        except OSError:
            pass

    # --- setup dialog
    def open_setup(self):
        win = tk.Toplevel(self.root)
        win.title("Which apps are allowed?")
        win.geometry("360x500")
        win.transient(self.root)
        win.lift()
        win.focus_force()
        ttk.Label(win, text="Tick the apps you may use during focus:",
                  font=("Segoe UI", 10, "bold")).pack(pady=8)

        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=10)
        checks = {}
        for n in sorted(n for n in visible_app_names() if n not in SYSTEM_SAFE):
            v = tk.BooleanVar()
            ttk.Checkbutton(frame, text=n, variable=v).pack(anchor="w")
            checks[n] = v

        ttk.Label(win, text=f"Websites allowed in browser (up to {MAX_SITES}):").pack(pady=(10, 2))
        entries = []
        for _ in range(MAX_SITES):
            e = ttk.Entry(win, width=36)
            e.pack(pady=2)
            entries.append(e)

        row = ttk.Frame(win)
        row.pack(pady=8)
        ttk.Label(row, text="Duration in minutes:").pack(side="left")
        mins = tk.StringVar(value="25")
        ttk.Entry(row, textvariable=mins, width=5).pack(side="left", padx=4)

        def go():
            apps = {n for n, v in checks.items() if v.get()}
            sites = [s for s in (clean_domain(e.get()) for e in entries) if s]
            try:
                m = int(mins.get())
            except ValueError:
                m = 0
            if m < 1:
                messagebox.showwarning("Duration", "Enter a duration of at least 1 minute.", parent=win)
                return
            if not messagebox.askyesno(
                    "Are you sure?",
                    f"Focus mode will run for {m} minutes and CANNOT be stopped early.\n\nStart?",
                    parent=win):
                return
            win.destroy()
            self.begin(apps, sites, time.time() + m * 60)

        ttk.Button(win, text="Start", command=go).pack(pady=6, ipadx=10)

    # --- start / end
    def begin(self, apps, sites, end_time):
        self.allowed_apps = apps
        self.sites = sites[:MAX_SITES]
        self.end_time = end_time
        try:
            self.save_state()
        except Exception:
            log("save_state failed:\n" + traceback.format_exc())
        try:
            self.apply_sites()
        except Exception:
            log("apply_sites failed:\n" + traceback.format_exc())
            messagebox.showwarning("Focus Mode",
                                   "Could not lock the browser (see focus_mode_log.txt).\n"
                                   "Apps will still be blocked.")
        if apps & PROXY_BROWSERS:
            try:
                self.proxy.start()
                set_system_proxy(True)
            except Exception:
                log("proxy failed:\n" + traceback.format_exc())
        if apps & BROWSERS:
            self.sites_frame.pack(pady=4)
            for v, s in zip(self.site_vars, self.sites + [""] * MAX_SITES):
                v.set(s)
        self.active = True
        self.status.config(text="Focus mode is ON")
        self.btn.pack_forget()
        threading.Thread(target=self._enforce, daemon=True).start()
        self._tick()

    def apply_sites(self):
        self.proxy.allowed = set(self.sites)
        if self.allowed_apps & set(POLICY_BROWSERS):
            apply_browser_policies(self.sites)

    def update_sites(self):
        self.sites = [s for s in (clean_domain(v.get()) for v in self.site_vars) if s][:MAX_SITES]
        self.apply_sites()
        self.save_state()

    def finish(self):
        self.active = False
        if self.proxy.running:
            self.proxy.stop()
        clear_restrictions()
        self.delete_state()
        self.sites_frame.pack_forget()
        self.status.config(text="Focus mode is OFF")
        self.timer_lbl.config(text="")
        self.btn.pack(pady=12, ipadx=12, ipady=6)
        messagebox.showinfo("Focus Mode", "Time's up! Focus session finished.")

    def _enforce(self):
        keep = self.allowed_apps | SYSTEM_SAFE
        while self.active:
            try:
                close_disallowed(keep, self.protected)
            except Exception:
                log("enforce error:\n" + traceback.format_exc())
            time.sleep(1.0)

    def _tick(self):
        if not self.active:
            return
        left = int(self.end_time - time.time())
        if left <= 0:
            self.finish()
            return
        h, rem = divmod(left, 3600)
        self.timer_lbl.config(text=f"Time left: {h:02d}:{rem // 60:02d}:{rem % 60:02d}")
        self.root.after(1000, self._tick)

    def on_close(self):
        if self.active:
            self.root.iconify()  # can't quit during focus
        else:
            self.root.destroy()


def main():
    if "--reset" in sys.argv:  # emergency cleanup
        clear_restrictions()
        FocusApp.delete_state()
        return
    root = tk.Tk()

    def on_error(exc, val, tb):
        log("".join(traceback.format_exception(exc, val, tb)))
        messagebox.showerror("Focus Mode error",
                             f"{val}\n\nDetails saved in:\n{LOG_FILE}")

    root.report_callback_exception = on_error
    FocusApp(root)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        err = traceback.format_exc()
        log(err)
        try:
            r = tk.Tk()
            r.withdraw()
            messagebox.showerror("Focus Mode failed to start", err + "\n\nLog: " + LOG_FILE)
        except Exception:
            pass
