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
- [x] **Screenshots for the README**: done, in `docs/screenshot-*.png`, with made-up data so nothing private
      shows. To redo them after a UI change: `py -3.12 tools\demo_server.py` (phone page at
      http://localhost:8796, PIN printed; capture it at 390x844), `py -3.12 tools\demo_windows.py` (the two PC
      windows, don't touch the mouse while it runs), then `py -3.12 tools\make_social_preview.py`.
- [ ] **Social preview**: upload `docs/social-preview.png` in the repo's Settings › General › Social preview
      (the picture shown when the link is shared).
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

1. Bump `VERSION` in `server.py`, `installer/setup.pyw` and `tests/exe_test.py`, and add a `## X.Y.Z`
   section to `CHANGELOG.md`.
2. Test locally: `py -3.12 tests\run_all.py --all` (build first with `py -3.12 installer\build.py`).
3. Commit, push, then tag: `git tag vX.Y.Z` and `git push origin vX.Y.Z`.
4. The **release** workflow (Actions tab) runs the tests, builds the installer on GitHub, signs it (once
   SignPath is set up, see below) and creates a **draft** release with the installer, the CHANGELOG part,
   the SHA-256 and the code signing policy link.
5. Check the draft and click Publish.

`installer\build.py app` and `installer\build.py setup` run the two halves of the build separately; the
workflow signs `Kovrel.exe` between them so the installer contains the signed app.

## 4. Windows warnings (SmartScreen and antivirus)

- Releases up to 2.0.1 aren't code-signed, so Windows shows "Windows protected your PC" until enough
  people have downloaded them. The README explains *More info › Run anyway*.
- **Free signing for open source (SignPath Foundation)**: the workflow and the README's
  "Code signing policy" section are ready. What only you can do:
  1. Turn on two-factor authentication on GitHub (SignPath requires it for everyone on the team).
  2. Apply at [signpath.org/apply](https://signpath.org/apply) with the repo URL. They check the
     [conditions](https://signpath.org/terms) by hand, which can take a few weeks.
  3. When accepted, in SignPath: link the **GitHub.com** trusted build system to the project, and create two
     artifact configurations with the slugs `app` and `setup`, pasting
     [`.signpath/artifact-configurations/app.xml`](../.signpath/artifact-configurations/app.xml) and
     [`setup.xml`](../.signpath/artifact-configurations/setup.xml).
  4. Create an API token for a CI user that may submit to the `release-signing` policy.
  5. On GitHub, Settings › Secrets and variables › Actions:
     - secret `SIGNPATH_API_TOKEN` = that token
     - variable `SIGNPATH_ORGANIZATION_ID` = your SignPath organization ID
     - optional variables `SIGNPATH_PROJECT_SLUG` (default `kovrel`) and `SIGNPATH_POLICY_SLUG`
       (default `release-signing`) if SignPath named them differently
  6. From then on each release asks you to approve two signing requests in SignPath (app, then installer).
     The workflow waits up to an hour for each.

  Until the variable is set, the workflow still builds and drafts releases, just unsigned.
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
