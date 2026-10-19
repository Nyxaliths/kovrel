# Changelog

## 2.0.2

- **Away mode helps you get Tailscale.** On the PC window, **Away** now shows an **Install Tailscale** button
  when Tailscale isn't installed on the PC, and an **Install Tailscale on your phone** button (with a QR code to
  scan) once it is. It used to be only in Settings › Remote.
- A small **Support Kovrel · buy me a coffee** link on the PC window and on the phone's Settings page. It only
  opens a web page when you click it.
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
