"""H.264 encoder (Windows Media Foundation): keyframes carry SPS+PPS, later frames are small, and it is fast enough.
Skipped (counts as a pass) where Windows has no H.264 encoder, e.g. on CI runners."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import h264  # noqa: E402

passed = failed = 0


def check(ok, name):
    global passed, failed
    print(("OK   " if ok else "FAIL ") + name)
    passed, failed = passed + bool(ok), failed + (not ok)


def picture(w, h, t):
    """A moving gradient with a box on it, as BGRA bytes."""
    row = bytes(b for x in range(w) for b in ((x + t * 4) & 255, (x // 3) & 255, 90, 255))
    img = bytearray(row * h)
    for y in range(80, 160):
        o = (y * w + 100 + t * 3) * 4
        img[o:o + 160 * 4] = bytes((255, 255, 255, 255)) * 160
    return bytes(img)


# Colours: white/black/red/grey must land on the video-range values decoders expect (BT.709 at 720p and up).
def nv12_of(rgb, w=128, h=720):
    b, g, r = rgb[2], rgb[1], rgb[0]
    nv = h264._rgb_to_nv12(bytes((b, g, r, 255)) * (w * h), w, h)
    return nv[0], nv[w * h], nv[w * h + 1]        # Y, Cb, Cr of the first pixel


for name, rgb, want in (("white", (255, 255, 255), (235, 128, 128)), ("black", (0, 0, 0), (16, 128, 128)),
                        ("red", (255, 0, 0), (63, 102, 240)), ("mid grey", (128, 128, 128), (126, 128, 128))):
    got = nv12_of(rgb)
    check(all(abs(a - b) <= 2 for a, b in zip(got, want)), f"{name} converts to Y/Cb/Cr {want} (got {got})")

if not h264.available():
    print("SKIP no H.264 encoder on this PC")
    print("\nRESULT: all passed (skipped)")
    sys.exit(0)

W, H = 640, 360
enc = h264.H264Encoder(W, H, fps=30, bitrate=2_000_000)
frames = []
t0 = time.perf_counter()
for t in range(40):
    frames += enc.encode(picture(W, H, t), W, H)
took = time.perf_counter() - t0
check(len(frames) == 40, f"one output per input, no startup delay ({len(frames)} of 40)")
check(not enc.rejected, f"the encoder accepted every setting ({[str(r[0]) for r in enc.rejected]})")
first = frames[0]
kinds = [k for k, _, _ in h264.split_nals(first[1])]
check(first[0] and 7 in kinds and 8 in kinds and kinds.index(7) < kinds.index(8) < kinds.index(5),
      f"first frame is a keyframe with SPS, PPS, then IDR (got {kinds})")
check(h264.codec_string(first[1]), "codec string readable from the SPS: " + str(h264.codec_string(first[1])))
check(all(5 not in [k for k, _, _ in h264.split_nals(d)] for key, d in frames[1:] if not key), "no IDR slices in delta frames")
check(sum(1 for key, _ in frames if key) <= 2, "no keyframe storm: " + str(sum(1 for key, _ in frames if key)) + " in 40 frames")
check(took / 40 < 0.05, f"encodes fast enough: {took / 40 * 1000:.1f} ms per 640x360 frame")

enc.force_keyframe()
more = enc.encode(picture(W, H, 41), W, H)
check(len(more) == 1 and more[0][0], "force_keyframe makes the next frame a keyframe")
enc.set_bitrate(500_000)
check(enc.bitrate == 500_000, "bitrate can be changed on the fly")

# odd sizes are cropped to even ones
enc2 = h264.H264Encoder(641, 361)
out = enc2.encode(picture(641, 361, 0), 641, 361)
check(len(out) == 1 and out[0][0] and (enc2.w, enc2.h) == (640, 360), "odd picture size is cropped to even")
enc.close()
enc2.close()

print(f"\n{passed} checks, {failed} failed")
print("RESULT: all passed" if not failed else "RESULT: FAILED")
sys.exit(1 if failed else 0)
