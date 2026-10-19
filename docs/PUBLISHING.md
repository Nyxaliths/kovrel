# Publishing Kovrel: step by step

Everything in the code is ready. These steps need you: your accounts, your name, your decision.
Nothing here has been done yet.

## 1. Before anything goes public

- [ ] **Domain**: check whether `kovrel.com` / `kovrel.app` are free at a registrar (for example
      Cloudflare or Porkbun). A web search in October 2026 found no product, company, app or GitHub project
      called Kovrel; the closest were Kovri (an I2P router), krel (a Kubernetes tool) and Kovaltry (a medicine).
      That is not a formal trademark search: for that, search "Kovrel" at
      [USPTO](https://tmsearch.uspto.gov) and [EUIPO](https://euipo.europa.eu/eSearch).
- [ ] **Your name in the code**: the commits in this repository were made as `kajih <kajihu06@gmail.com>`.
      If you don't want that email public, either
      - turn on *Keep my email address private* on GitHub and start a fresh history before pushing:
        ```powershell
        git checkout --orphan release
        git -c user.email=YOUR-ID+USERNAME@users.noreply.github.com commit -m "Kovrel 2.0.0"
        ```
      - or rewrite the author of the existing commits (`git filter-repo --mailmap`).
- [ ] **Copyright line**: `LICENSE` says "The Kovrel authors". Put your name or GitHub username there if you prefer.
- [ ] **Screenshots for the README**: take them on your phone with nothing private on screen (no personal
      PC name, music or notifications), save them as `docs/screenshot-*.png`, and add them under the title in
      `README.md`.
- [ ] **Test on your real phone** (see "Still to verify" below).

## 2. Put it on GitHub

1. github.com › New repository › name `kovrel`, **Public**, *no* README/licence (they're already here).
2. In this folder:
   ```powershell
   git remote add origin https://github.com/USERNAME/kovrel.git
   git push -u origin master
   ```
3. Repository settings:
   - About: description "Control your Windows PC from your phone. No app, no account, no cloud.";
     topics `remote-control windows phone tailscale`
   - Security › enable **Private vulnerability reporting** (SECURITY.md points there)
   - The `tests` workflow runs on every push (Actions tab). It should be green.

## 3. Make a release

```powershell
py -3.12 tests\run_all.py --all      # everything, including the installed-exe test
py -3.12 installer\build.py          # -> build\Kovrel-Setup.exe
Get-FileHash build\Kovrel-Setup.exe -Algorithm SHA256
```

Then github.com › Releases › *Draft a new release*: tag `v2.0.0`, title `Kovrel 2.0.0`, paste the 2.0.0
part of `CHANGELOG.md`, attach `Kovrel-Setup.exe`, and add the line `SHA-256: <the hash>`.

## 4. Windows warnings (SmartScreen and antivirus)

- The installer isn't code-signed, so Windows shows "Windows protected your PC" until enough people have
  downloaded it. The README explains *More info › Run anyway*.
- **Free signing for open source**: apply at [SignPath Foundation](https://signpath.org) once the repo is public
  and has a release. They sign builds made by GitHub Actions. That needs a release workflow (build on
  GitHub, not on your PC); ask for it when you get there.
- **Antivirus false positives**: remote-control tools and PyInstaller apps are often flagged. Before
  announcing a release, upload `Kovrel-Setup.exe` to [VirusTotal](https://www.virustotal.com). If engines
  flag it, submit it as a false positive to them (Microsoft: [submit a file](https://www.microsoft.com/wdsi/filesubmission)).

## 5. Telling people about it

Be upfront in any post: it's for **your own PC**, it uses `SendInput` (games with kernel anti-cheat may
block it), and remote access goes through Tailscale (nothing opened to the internet).

## Still to verify on real hardware

These couldn't be tested from the PC alone:
- [ ] Phone: sign in, the Allow pop-up, the new look, Add to Home Screen shows the K logo
      (delete the old home-screen icon first; iPhones cache it)
- [ ] HTTPS on a real phone (one-time warning)
- [ ] Remote access: install Tailscale on PC + phone, turn on remote, use it over mobile data;
      then Get certificate (needs HTTPS Certificates on in the Tailscale admin console)
- [ ] Virtual controller with ViGEmBus installed
- [ ] PC sound on iPhone, a large file both ways, autostart after a reboot

## Not built (on purpose, for later)

- **Auto-update**: needs a place to host releases and signature checking; design it together with signing.
- Mac/Linux versions, screen recording, H.264 video (needs trusted HTTPS: possible now with Tailscale's certificate).
