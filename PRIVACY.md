# Privacy

**Kovrel has no servers, no accounts, no analytics and no tracking.** Nothing about you or your PC is
sent to the people who make Kovrel, because there is nowhere to send it.

## What travels where

- Your phone talks directly to your PC: on your home network, or through your own Tailscale network
  if you turned on remote access. The screen picture, sound, clicks, typing and files go only between
  those two devices.
- The phone page is served by your PC and loads nothing from the internet (no fonts, scripts or images
  from other sites).
- Kovrel itself contacts the internet in only three cases: once a day it asks GitHub whether a newer
  version exists (an ordinary web request to api.github.com with nothing about you in it; turn it off in
  **Settings › Access › Updates**), when you ask it to get a Tailscale certificate (that goes through the
  Tailscale app on your PC), and when a button or macro you made opens a website.
- Updates never install by themselves: Kovrel only shows "update available" and opens the release page.
- Tailscale is a separate service with its own privacy policy. Kovrel only asks the Tailscale app on your
  PC for its status and, if you click **Get certificate**, for a certificate.

## What is stored on the PC

In `%APPDATA%\Kovrel`:
- `config.json`: settings, the PIN or password (encrypted for your Windows user), trusted phones
  (name, last address, last use, a hash of each session token), your buttons, game layouts, shared folders
  and wake targets
- `activity.log`: sign-ins and important actions, without PINs, passwords or tokens
- `log.txt`: error messages
- certificates, if you turned on HTTPS

Files you send from the phone go to `Downloads\Kovrel` (or the shared folder you had open).

## What is stored on the phone

The browser's local storage for the PC's address holds a random device ID, the session token, the
device name you typed, and display and controller preferences. **Settings › Sign out** on the phone
deletes the session.

## Removing everything

Uninstall Kovrel from **Windows Settings › Apps**. That deletes the program and `%APPDATA%\Kovrel`.
Files in `Downloads\Kovrel` are kept. On the phone, remove the home-screen icon and clear the site's
data in the browser.
