# Changelog

## 2.2.1

Fixes and polish from a full read-through of the code. No new settings.

- **Drag on the picture.** With Left (or Right, Middle) held by a double-tap, a finger on the Screen
  picture now drags the cursor there, so you can move a window or a file by pointing at where it
  should go. Before, a touch on the picture clicked and quietly let go of the button while the
  phone still said "held".
- **Sending files tells the truth.** A file that failed to send used to end with "File sent". Now
  the phone says what went wrong (and how many did arrive), and the progress bar starts from zero
  for each file.
- "Reconnected" no longer pops up every time you come back to the page, only after the connection
  was really lost.
- The More page's tiles fit narrow phones: "Notifications" no longer runs into the edge.
- Settings › Connected now marks the right phone as "this one" when two phones share a name.
- The frame counter says "Paused" only when you paused the screen, not while it is still connecting.
- Typing text that came from Windows (copied with its line endings) pressed Enter twice per line.
- Small things: the tour starts on **Next**, the key hints under buttons (Ctrl C, Space…) use the
  right size, the sleep timer reads "1 h" instead of "1 h 0 min", the share window's copy button
  has a copy icon, and the backspace key is announced by screen readers.
- PC window: turning the update check back on looks for an update right away; "Stay signed in"
  shows "1 day" correctly.
- Under the hood: text from the PC is always escaped before it is shown on the status page, two
  sign-in checks can no longer overlap while a phone waits for approval, and a monitor that refuses
  a brightness change no longer leaks a handle.
- For people building Kovrel: the installed-app test (`tests\exe_test.py`) used to run the uninstaller
  against the real Desktop, Start menu and Startup shortcuts and the Settings › Apps entry. The
  uninstaller now takes `-ShortcutDir`, `-NoRegister` and `-KeepRunning`, and the test uses them.

## 2.2.0

- **Disconnect a phone from the PC window.** Each connected phone in the main window now has a
  **Disconnect** button. It signs that phone out and cuts its screen and sound right away; it has to be
  approved again to come back. (Other phones see a short blip and reconnect by themselves.)
- **Share to Kovrel (Android).** Share a link or text from any app to Kovrel, then tap **Open the link on
  the PC** or **Copy to the PC's clipboard**. Nothing happens on the PC until you tap. It needs Kovrel added
  to the home screen over a trusted secure address (Tailscale's certificate); iPhones don't support it.
- **The phone says when the PC is locked**, instead of showing a black screen that ignores taps, and says the
  PC may be asleep, off or on another network when it can't be reached for a while.
- **A short tour for new phones**: three screens on what Kovrel does, what stays private and how you stay in
  control. **Settings › Take the tour again** shows it any time.

## 2.1.0

- **Kovrel is on Instagram.** The phone's More page has a **Follow Kovrel** row that opens @kovrelapp. It
  only opens a web page when you tap it.
- **Fewer antivirus false alarms.** Both exes are now built with a PyInstaller loader compiled for Kovrel
  instead of the stock one that countless other programs share, which some antivirus engines flag on sight.

## 2.0.2

- **Away mode helps you get Tailscale.** On the PC window, **Away** now shows an **Install Tailscale** button
  when Tailscale isn't installed on the PC, and an **Install Tailscale on your phone** button (with a QR code to
  scan) once it is. It used to be only in Settings › Remote.
- A small **Support Kovrel · buy me a coffee** link on the PC window and on the phone's More page (and in its
  Settings). It only opens a web page when you click it.
- **The phone page now reloads itself after the PC app is updated**, so a page left open (or added to the home
  screen) no longer keeps showing old buttons. Reload it once by hand when you first update to 2.0.2.
- The PC window now fits its content, so nothing at the bottom is cut off.
- **Smoother screen with less data: H.264 video.** When the phone's page is secure (HTTPS or Tailscale's
  certificate) and Windows has its built-in H.264 encoder, the screen is sent as H.264 instead of JPEG
  pictures, using far less data for the same sharpness. Nothing is bundled: it uses Windows' own encoder.
  Otherwise Kovrel stays on JPEG, with no setting to touch. **Screen settings › Video** can force JPEG.
- The mouse pad page no longer has a separate "Hold click" button. Double-tap Left, Middle or Right to hold
  it (as on the Screen tab) and tap again to let go.

## 2.0.1

- The PC app tells you when a new version is out (asks GitHub once a day; opens the download page,
  never installs anything by itself; can be turned off in Settings › Access)

## 2.0.0

The app is now called **Kovrel** (it was Vexari). Installing 2.0 replaces Vexari and keeps your PIN,
approved phones, buttons, game layouts and received files. Approved phones stay signed in.

- **Remote access through Tailscale**: off by default, needs a 12+ character password, new phones only
  need Allow on the PC, away sessions lock when unused, optional trusted HTTPS certificate from Tailscale
- Never answers the open internet (only home-network and Tailscale addresses)
- New, simpler look: one mint accent, flat surfaces, bigger buttons and text; new logo
- Screen tab fills the page: a big mouse pad, no scrolling, and its own layouts for an upright or
  sideways phone and for portrait PC monitors; zooming no longer pushes the controls away
- Double-tap Left or Right to hold the button and drag with the mouse pad; tap again to let go
- Dragging now works everywhere (windows, text, files): the cursor moves like a real mouse
- Keyboard button on the Screen tab: a small text box with Send and Enter/Backspace/Esc/Tab/Space
- Remote access: a new phone can be approved through Tailscale too (it still needs Allow on the PC)
- The home-screen icon now shows the logo on iPhone and Android instead of a letter, also with the
  secure connection turned on
- Large downloads go straight to the phone's storage instead of the page's memory
- The phone page no longer loads its font from the internet
- Fixes: the installer's error page crashed instead of explaining the problem; a malformed request
  could hang a connection; the Devices list lost its labels after removing a device

## 1.2.0

Vexari: push video stream, per-app volume, brightness, programs, folders, macros, game profiles,
virtual controller, wake-on-LAN, device approval, sessions, HTTPS, activity log.
