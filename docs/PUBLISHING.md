# Maintaining and releasing Kovrel

Notes for whoever looks after the repository: how a release is made, what the GitHub settings are, and
what is still open.

## The repository

Already set up:
- Public at github.com/Nyxaliths/kovrel, MIT licence, history under a GitHub no-reply address.
- About text and topics, **Private vulnerability reporting** on (SECURITY.md points there).
- Social preview: `docs/social-preview.png` (Settings › General › Social preview).
- `.github/FUNDING.yml` shows the Sponsor (Buy Me a Coffee) button.
- Workflows: **tests** runs on every push and pull request; **release** runs on a `vX.Y.Z` tag.

Still open (optional):
- **Domain**: check whether `kovrel.com` / `kovrel.app` are free. A web search in October 2026 found no
  product, company, app or GitHub project called Kovrel; that is not a formal trademark search (for that,
  search "Kovrel" at [USPTO](https://tmsearch.uspto.gov) and [EUIPO](https://euipo.europa.eu/eSearch)).
- **Copyright line**: `LICENSE` says "The Kovrel authors". Put a name or GitHub username there if you prefer.

## Make a release

1. Bump the version in `server.py` (`VERSION`), `installer/setup.pyw` and `tests/exe_test.py`, and add a
   `## X.Y.Z` section to `CHANGELOG.md`. The release notes are taken from that section.
2. Test locally: `py -3.12 installer\build.py`, then `py -3.12 tests\run_all.py --all`.
3. Merge everything into `main` through pull requests. Open each pull request against `main`: a pull
   request based on another branch merges into that branch, not into `main`.
4. Tag `main`: `git tag -a vX.Y.Z -m "Kovrel X.Y.Z"` and `git push origin vX.Y.Z`. The tag must match
   `VERSION`, or the workflow stops.
5. The **release** workflow runs the tests, builds the installer on GitHub, signs it (once SignPath is set
   up, see below) and creates a **draft** release. The notes are written for you: what Kovrel is, how to
   install it (including the SmartScreen step), what's new, the SHA-256 and a link to "Code signing".
6. Check the draft on the Releases page (edit it from its pencil icon) and click **Publish**. Copies of
   Kovrel that have the update check on show "update available" within a day.

`installer\build.py app` and `installer\build.py setup` run the two halves of the build separately; the
workflow signs `Kovrel.exe` between them so the installer contains the signed app.

## Logo, icons and screenshots

`py -3.12 tools\make_icon.py` redraws the logo (`assets/logo.png`, `assets/Kovrel.ico`) and the phone's
home-screen icons in `web/`.

The images in `docs/` use made-up data so nothing private shows. To redo them after a UI change:
`py -3.12 tools\demo_server.py` (phone page at http://localhost:8796, PIN printed; capture it at 390x844),
`py -3.12 tools\demo_windows.py` (the two PC windows; don't touch the mouse while it runs), then
`py -3.12 tools\make_social_preview.py`, and upload the new social preview in the repository settings.

## Windows warnings (SmartScreen and antivirus)

- No release is code-signed, so Windows shows "Windows protected your PC" until enough people have
  downloaded them. The README and the release notes explain *More info › Run anyway*.
- **Free signing for open source (SignPath Foundation)**: applied on 2026-10-19 and **declined the same
  day** because the project doesn't yet show enough public use (stars, forks, contributors, articles,
  discussions). They invite a new application once it does; a paid SignPath subscription would also work
  (docs.signpath.io/change-subscription). Two-factor sign-in on GitHub is already on. The workflow is ready
  for when that changes (and the README's "Code signing" section would then need the SignPath attribution
  and team-role wording from their [conditions](https://signpath.org/terms)). Steps then:
  1. Apply again at [signpath.org/apply](https://signpath.org/apply) with the repo URL.
  2. When accepted, in SignPath: link the **GitHub.com** trusted build system to the project, and create two
     artifact configurations with the slugs `app` and `setup`, pasting
     [`.signpath/artifact-configurations/app.xml`](../.signpath/artifact-configurations/app.xml) and
     [`setup.xml`](../.signpath/artifact-configurations/setup.xml).
  3. Create an API token for a CI user that may submit to the `release-signing` policy.
  4. On GitHub, Settings › Secrets and variables › Actions:
     - secret `SIGNPATH_API_TOKEN` = that token
     - variable `SIGNPATH_ORGANIZATION_ID` = your SignPath organization ID
     - optional variables `SIGNPATH_PROJECT_SLUG` (default `kovrel`) and `SIGNPATH_POLICY_SLUG`
       (default `release-signing`) if SignPath named them differently
  5. From then on each release asks you to approve two signing requests in SignPath (app, then installer).
     The workflow waits up to an hour for each.

  Until the variable is set, the workflow still builds and drafts releases, just unsigned.
- **Antivirus false positives**: remote-control tools and PyInstaller apps are often flagged. Before
  announcing a release, upload `Kovrel-Setup.exe` to [VirusTotal](https://www.virustotal.com). If engines
  flag it, submit it as a false positive to them (Microsoft: [submit a file](https://www.microsoft.com/wdsi/filesubmission)).

## Telling people about it

Be upfront in any post: it's for **your own PC**, it uses `SendInput` (games with kernel anti-cheat may
block it), and remote access goes through Tailscale (nothing opened to the internet).

## Still to verify on real hardware

These couldn't be tested from the PC alone:
- [ ] Virtual controller with ViGEmBus installed
- [ ] PC sound on iPhone, a large file both ways, autostart after a reboot
- [ ] H.264 video on a real iPhone (needs iOS 16.4+ and the HTTPS page; checked in a desktop browser only):
      Screen tab and Game, the "Video" setting, and that it falls back to JPEG on plain http

## Not built (on purpose, for later)

- **Auto-install of updates**: Kovrel only says an update is out and opens the release page. Installing by
  itself would need signed releases and signature checking; design it together with signing.
- Mac/Linux versions, screen recording.
