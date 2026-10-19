# Security

Kovrel gives a phone full control of a PC, so a signed-in phone can do anything you could do at the
keyboard. The design goal is that only your own phones can ever get that far.

## Reporting a problem

Please don't open a public issue for a security problem. Use GitHub's **Report a vulnerability**
(Security tab › Advisories) so it can be fixed before it's public. Include steps to reproduce and the
Kovrel version (shown at the bottom of the phone's Settings page).

## How it's protected

**Who can connect**
- Kovrel answers only on the home network (private addresses) and, when remote access is on, on
  Tailscale addresses (100.64.0.0/10). Requests from any other address are refused before anything
  else happens, so forwarding the port on a router does not expose it.
- Remote access is off by default. It can only be turned on with a password of 12+ characters, and it
  turns itself off if the sign-in goes back to a PIN or a shorter password.

**Signing in**
- The 6-digit PIN (or password) is only used to sign in. It is stored encrypted with Windows DPAPI,
  for this Windows user only.
- Every new phone must also be approved on the PC itself ("Allow this device?"). Approval requests
  expire after 90 seconds; a denied phone is blocked for 5 minutes. This also applies through
  Tailscale: away from home nobody is at the PC to click Allow, so new phones can't get in.
- After approval, each phone gets its own random session token (only a SHA-256 hash is stored on the PC).
  Sessions expire after 1–90 days unused (you choose). Away from home they lock after 15–240 minutes
  unused, and the phone has to enter the password again.
- Wrong guesses: 5 per address, then a lockout of 1, 2, 4… up to 15 minutes. 25 wrong guesses from
  anywhere within 10 minutes pause all sign-ins for 5 minutes.
- Devices can be removed one by one or all at once in **Settings › Devices**.

**While connected**
- **Pause** (main window or tray menu) blocks all control at once. Phones can still look, but not act.
- Shared folders: phones can only browse, download and upload inside folders you shared. Paths are
  resolved and checked, so `..`, links and absolute paths can't escape.
- Downloads use one-time links that expire after 60 seconds.
- Windows' own processes can't be closed from the phone.
- An *automation key* (for iPhone Shortcuts, Tasker…) can only run your own buttons.
- The page is served with a strict Content-Security-Policy, `X-Frame-Options: DENY` and no referrer.
  It loads nothing from the internet. API calls need a custom header, which a web page from another
  site can't send.
- The activity log (**Settings › Activity**) records sign-ins, approvals, power actions, closed programs,
  file transfers and setting changes. It never contains PINs, passwords or tokens.

**Encryption**
- At home, traffic is plain HTTP unless you turn on **Settings › Access › secure connection (HTTPS)**.
  The certificate is self-signed by your PC (TLS 1.2+), so phones show a one-time warning.
- Over Tailscale, traffic is always encrypted by Tailscale (WireGuard). Kovrel can also use Tailscale's
  publicly trusted certificate, so there is no warning.

## What it does not protect against

- Someone who already has access to your Windows account (they can read the PIN in the Kovrel window).
- A phone you approved that is later stolen while still signed in. Remove it in **Settings › Devices**.
- Malware on the phone or the PC.
- Other devices on the same Wi-Fi watching unencrypted traffic, if HTTPS is off. Turn it on for shared Wi-Fi.
