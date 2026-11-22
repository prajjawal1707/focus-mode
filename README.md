# Focus Mode v2 (Windows)

Focus Mode is a small local desktop app for Windows that helps you stay focused for a chosen amount of time. You choose which currently open apps may remain available, up to three allowed website domains, and a session duration.

> **Important:** This app is intentionally restrictive. Once a session starts, the app does not provide a normal early-stop button. It attempts to close disallowed application windows and restrict browser access until the timer expires. Read the limitations and recovery instructions below before using it.

## Features

- Choose which currently visible applications are allowed during a focus session.
- Allow up to **three website domains**.
- Set a session duration in minutes.
- For Chrome, Microsoft Edge, and Brave, apply Windows browser policies that block other URLs and disable private/incognito mode.
- For Firefox, Opera, and Vivaldi, use a local proxy to allow only the configured domains.
- Save the active session state so the app can attempt to resume the session after it is reopened or Windows restarts.
- Write diagnostic messages to a log file.

## Requirements

- Windows (the program uses Windows-specific APIs and the Windows Registry).
- Python 3.9 or newer recommended.
- `psutil` Python package.
- Tkinter, normally included with the standard Windows Python installer.

This is not a macOS or Linux application.

## Download and setup

1. Download the project code and save the Python source as **`focus_mode.pyw`**.
   - The `.pyw` extension runs the app without opening a separate console window.
   - Make sure the file contains the actual Python source code, not a Markdown-formatted copy with line numbers or formatting characters.
2. Install Python for Windows from [python.org](https://www.python.org/downloads/).
   - During installation, enable **Add Python to PATH**.
   - Use a standard installation that includes Tcl/Tk (the default Windows installer normally does).
3. Open **Command Prompt** or **PowerShell**.
4. Install the required package:

   ```powershell
   py -m pip install --upgrade pip
   py -m pip install psutil
   ```

5. Put `focus_mode.pyw` somewhere you can find it, such as a folder named `FocusMode` in Documents.
6. Double-click **`focus_mode.pyw`** to launch the app.

If double-clicking does not launch it, open Command Prompt in the folder containing the file and run:

```powershell
pyw .\focus_mode.pyw
```

To see startup errors in a console, run:

```powershell
py .\focus_mode.pyw
```

## Starting a focus session

1. Launch `focus_mode.pyw`.
2. Select **Start Focus Mode**.
3. Tick the applications that you want to keep available.
4. Enter up to three website domains, for example:
   - `wikipedia.org`
   - `github.com`
   - `docs.python.org`
5. Enter the session duration in minutes.
6. Review the confirmation prompt carefully.
7. Select **Start** to begin.

The app's setup accepts domain names or URLs and normalizes them to host names. A subdomain of an allowed domain is also permitted by the local proxy's domain check.

While a session is active, closing the main window minimizes it to the taskbar rather than exiting the app. The timer controls when the session ends.

## Ending a session and recovery

- **Normal completion:** Wait for the countdown to reach zero. The app then attempts to remove the browser restrictions and clear its saved session state.
- **After a restart:** If the saved session has not expired, launching the app again attempts to resume it.
- **Emergency reset:** If the app is not active but restrictions appear to remain, open Command Prompt in the folder containing `focus_mode.pyw` and run:

  ```powershell
  py .\focus_mode.pyw --reset
  ```

  This invokes the script's cleanup routine and removes its saved session file. It is a recovery mechanism, not a guaranteed rollback of every Windows or browser setting.

## Files the app creates

The app stores its runtime files in your Windows user profile:

- `%USERPROFILE%\.focus_mode_state.json` — saved session end time, allowed apps, and allowed sites.
- `%USERPROFILE%\focus_mode_log.txt` — diagnostic log.

You can open the log with Notepad if startup or enforcement errors occur.

## Important limitations and cautions

- **Use at your own risk.** This program changes per-user browser policy registry keys and the Windows Internet proxy setting.
- **Back up existing settings first.** The cleanup code removes the URL allow/block policy keys and private-mode policy values used by the app. It does not preserve and restore pre-existing values that may have been configured by you or your organization.
- **Proxy settings:** The cleanup routine disables the Windows Internet proxy setting; it does not restore a previously configured proxy server or its previous enabled state.
- **Application enforcement:** The app attempts to close disallowed visible windows by sending a window-close message. It is not a security boundary and does not guarantee that every process or background task is stopped.
- **Browser coverage:** The listed browsers are handled using different mechanisms. Other browsers are not specifically restricted by this code.
- **Network limitations:** The local proxy is a basic HTTP/HTTPS CONNECT proxy, not a full network firewall. It does not guarantee that every browser, app, DNS request, or alternate network path is blocked.
- **No early-stop control:** The normal interface intentionally does not offer a stop button during a session. Check your allowed apps, sites, and duration before confirming.
- **Managed devices:** Do not run this on a work- or school-managed computer without permission. Organization-managed browser policies may conflict with the app.
- **Keep a recovery path:** Save this README and know how to run the `--reset` command before starting a session.

## Troubleshooting

### `ModuleNotFoundError: No module named 'psutil'`

Run:

```powershell
py -m pip install psutil
```

### `No module named '_tkinter'` or Tkinter cannot start

Reinstall Python using the standard Windows installer with Tcl/Tk support enabled.

### The browser does not follow the allowed-site list

Check that you entered valid domains, close and reopen the affected browser, and review `focus_mode_log.txt`. Browser policies can be affected by existing organization policies or browser versions. Firefox, Opera, and Vivaldi use the local proxy path and may require the Windows proxy setting to be honored by the browser.

### Restrictions remain after a problem

Try the emergency reset command:

```powershell
py .\focus_mode.pyw --reset
```

Then inspect `%USERPROFILE%\focus_mode_log.txt`. If you had a proxy configured before using the app, you may need to restore that proxy manually in Windows Settings.
