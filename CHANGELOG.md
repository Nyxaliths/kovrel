# Changelog

## 2.0.0 (unreleased)

The app is now called **Kovrel** (it was Vexari). Installing 2.0 replaces Vexari and keeps your PIN,
approved phones, buttons, game layouts and received files. Approved phones stay signed in.

- **Remote access through Tailscale**: off by default, needs a 12+ character password, new phones only
  need Allow on the PC, away sessions lock when unused, optional trusted HTTPS certificate from Tailscale
- Never answers the open internet (only home-network and Tailscale addresses)
- New, simpler look: one mint accent, flat surfaces, bigger buttons and text; new logo
- The home-screen icon now shows the logo on iPhone and Android instead of a letter
- Large downloads go straight to the phone's storage instead of the page's memory
- The phone page no longer loads its font from the internet
- Fixes: the installer's error page crashed instead of explaining the problem; a malformed request
  could hang a connection; the Devices list lost its labels after removing a device

## 1.2.0

Vexari: push video stream, per-app volume, brightness, programs, folders, macros, game profiles,
virtual controller, wake-on-LAN, device approval, sessions, HTTPS, activity log.
