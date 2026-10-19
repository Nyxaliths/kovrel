"""H.264 video for the screen stream, using the encoder that comes with Windows (Media Foundation).

Nothing is bundled: no codec library, no licence to carry. If Windows has no H.264 encoder (some
Windows Server and "N" editions), HAS_ENCODER stays False and the screen stream stays on JPEG.

    enc = H264Encoder(1280, 720, fps=30, bitrate=4_000_000)
    for is_key, annexb_bytes in enc.encode(bgra_bytes): ...

Frames come out as Annex B (start codes). Keyframes always begin with SPS + PPS so a decoder can start
at any keyframe. The caller must use one thread per encoder (Media Foundation is called from the
thread that created it).
"""
import ctypes
import struct
import uuid
from ctypes import POINTER, byref, c_char_p, c_long, c_uint32, c_uint64, c_ulong, c_void_p

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

HRESULT = ctypes.HRESULT
S_OK = 0
MF_E_TRANSFORM_NEED_MORE_INPUT = -1072861838   # 0xC00D6D72
MF_E_TRANSFORM_STREAM_CHANGE = -1072861849     # 0xC00D6D61
MF_VERSION = 0x00020070
MFT_OUTPUT_STREAM_PROVIDES_SAMPLES = 0x100
MFT_MESSAGE_NOTIFY_BEGIN_STREAMING = 0x10000000
MFT_MESSAGE_NOTIFY_START_OF_STREAM = 0x10000003
COINIT_MULTITHREADED = 0
CLSCTX_INPROC_SERVER = 1
VT_BOOL, VT_UI4 = 11, 19


class GUID(ctypes.Structure):
    _fields_ = [("Data1", c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]


def guid(text):
    return GUID.from_buffer_copy(uuid.UUID(text).bytes_le)


def media_guid(tag):    # the "{xxxxxxxx-0000-0010-8000-00AA00389B71}" family
    return guid("%08X-0000-0010-8000-00AA00389B71" % tag)


CLSID_H264_ENCODER = guid("6CA50344-051A-4DED-9779-A43305165E35")
IID_IMFTransform = guid("BF94C121-5B05-4E6F-8000-BA598961414D")
IID_ICodecAPI = guid("901DB4C7-31CE-41A2-85DC-8FA0BF41B8DA")
MF_MT_MAJOR_TYPE = guid("48eba18e-f8c9-4687-bf11-0a74c9f96a8f")
MF_MT_SUBTYPE = guid("f7e34c9a-42e8-4714-b74b-cb29d72c35e5")
MF_MT_FRAME_SIZE = guid("1652c33d-d6b2-4012-b834-72030849a37d")
MF_MT_FRAME_RATE = guid("c459a2e8-3d2c-4e44-b132-fee5156c7bb0")
MF_MT_PIXEL_ASPECT_RATIO = guid("c6376a1e-8d0a-4027-be45-6d9a0ad39bb6")
MF_MT_INTERLACE_MODE = guid("e2724bb8-e676-4806-b4b2-a8d6efb44ccd")
MF_MT_AVG_BITRATE = guid("20332624-fb0d-4d9e-bd0d-cbf6786c102e")
MF_MT_MPEG2_PROFILE = guid("ad76a80b-2d5c-4e0b-b375-64e520137036")
MF_MT_MPEG_SEQUENCE_HEADER = guid("3c036de7-3ad0-4c9e-9216-ee6d6ac21cb3")
MF_LOW_LATENCY = guid("9c27891a-ed7a-40e1-88e8-b22727a024ee")
CODECAPI_RATE_CONTROL = guid("1c0608e9-370c-4710-8a58-cb6181c42423")
CODECAPI_MEAN_BITRATE = guid("f7222374-2144-4815-b550-a37f8e12ee52")
CODECAPI_GOP_SIZE = guid("95f31b26-95a4-41aa-9303-246a7fc6eef1")
CODECAPI_B_PICTURES = guid("8d390aac-dc5c-4200-b57f-814d04babab2")
CODECAPI_FORCE_KEYFRAME = guid("398c1b98-8353-475a-9ef2-8f265d260345")
VIDEO, H264, NV12 = media_guid(0x73646976), media_guid(0x34363248), media_guid(0x3231564E)
PROFILE_MAIN = 77


class OutputBuffer(ctypes.Structure):    # MFT_OUTPUT_DATA_BUFFER
    _fields_ = [("stream", c_uint32), ("sample", c_void_p), ("status", c_uint32), ("events", c_void_p)]


class StreamInfo(ctypes.Structure):      # MFT_OUTPUT_STREAM_INFO
    _fields_ = [("flags", c_uint32), ("size", c_uint32), ("alignment", c_uint32)]


def _vtable(obj):
    return ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0]


def _call(obj, index, argtypes, *args, restype=HRESULT):
    return ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(_vtable(obj)[index])(obj, *args)


def _release(obj):
    if obj and obj.value:
        _call(obj, 2, (), restype=c_ulong)


try:
    _ole32 = ctypes.WinDLL("ole32")
    _mfplat = ctypes.WinDLL("mfplat")
    for _name, _types in (("MFStartup", (c_ulong, c_ulong)), ("MFCreateMediaType", (POINTER(c_void_p),)),
                          ("MFCreateSample", (POINTER(c_void_p),)), ("MFCreateMemoryBuffer", (c_ulong, POINTER(c_void_p)))):
        _fn = getattr(_mfplat, _name)
        _fn.restype, _fn.argtypes = HRESULT, _types
    _ole32.CoInitializeEx.restype = c_long
    _ole32.CoCreateInstance.restype = HRESULT
    _ole32.CoCreateInstance.argtypes = (POINTER(GUID), c_void_p, c_ulong, POINTER(GUID), POINTER(c_void_p))
    _AVAILABLE = True
except (OSError, AttributeError):   # not Windows, or no Media Foundation
    _AVAILABLE = False

_started = False


def _startup():
    global _started
    _ole32.CoInitializeEx(None, COINIT_MULTITHREADED)   # per thread; "already initialised" is fine
    if not _started:
        _mfplat.MFStartup(MF_VERSION, 0)
        _started = True


class VARIANT(ctypes.Structure):
    """A COM VARIANT (24 bytes on 64-bit Windows) holding a 16-bit bool or a 32-bit number."""
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                ("data", c_uint64), ("pad", c_uint64)]


def _variant(vt, value):
    return VARIANT(vt=vt, data=0xFFFF if vt == VT_BOOL and value else 0 if vt == VT_BOOL else int(value))


def split_nals(data):
    """Annex B bytes -> list of (nal_type, start_offset_of_start_code, nal_bytes_including_header)."""
    out, n, i = [], len(data), 0
    starts = []
    while i + 3 <= n:
        if data[i] == 0 and data[i + 1] == 0 and (data[i + 2] == 1 or (data[i + 2] == 0 and i + 3 < n and data[i + 3] == 1)):
            code = 3 if data[i + 2] == 1 else 4
            starts.append((i, i + code))
            i += code
        else:
            i += 1
    for k, (sc, body) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else n
        if body < end:
            out.append((data[body] & 0x1F, sc, data[body:end]))
    return out


def codec_string(annexb):
    """'avc1.PPCCLL' from the first SPS in the data (what WebCodecs wants), or None."""
    for kind, _, nal in split_nals(annexb):
        if kind == 7 and len(nal) >= 4:
            return "avc1.%02x%02x%02x" % (nal[1], nal[2], nal[3])
    return None


def _rgb_to_nv12(bgra, w, h):
    """BGRA bytes -> NV12 bytes (limited range). BT.709 for HD, BT.601 below, as decoders assume."""
    im = Image.frombuffer("RGB", (w, h), bgra, "raw", "BGRX", 0, 1)
    ym, cbm, crm = _MATRIX_709 if h >= 720 else _MATRIX_601
    y = im.convert("L", ym).tobytes()
    small = im.resize((w // 2, h // 2), Image.BOX)       # chroma is stored at half size: convert it there (4x less work)
    cb, cr = small.convert("L", cbm).tobytes(), small.convert("L", crm).tobytes()
    uv = bytearray(len(cb) * 2)
    uv[0::2], uv[1::2] = cb, cr
    return y + bytes(uv)


# (R, G, B, offset) rows for Y, Cb, Cr: video (limited) range, as H.264 decoders expect.
_MATRIX_709 = ((0.1826, 0.6142, 0.0620, 16), (-0.1006, -0.3386, 0.4392, 128), (0.4392, -0.3989, -0.0403, 128))
_MATRIX_601 = ((0.2568, 0.5041, 0.0979, 16), (-0.1482, -0.2910, 0.4392, 128), (0.4392, -0.3678, -0.0714, 128))


class H264Encoder:
    def __init__(self, width, height, fps=30, bitrate=4_000_000, gop_seconds=2):
        if not _AVAILABLE or Image is None:
            raise OSError("H.264 encoding isn't available here")
        self.w, self.h = width & ~1, height & ~1
        if self.w < 64 or self.h < 64:
            raise ValueError("picture too small")
        self.fps, self.bitrate, self.n = fps, bitrate, 0
        self.header = b""          # SPS + PPS, for keyframes that come without them
        self.rejected = []         # settings this Windows' encoder refused (for tests)
        self.mft = self.api = None
        _startup()
        try:
            self._open(gop_seconds)
        except Exception:
            self.close()
            raise

    # -- setup
    def _open(self, gop_seconds):
        p = c_void_p()
        _ole32.CoCreateInstance(byref(CLSID_H264_ENCODER), None, CLSCTX_INPROC_SERVER, byref(IID_IMFTransform), byref(p))
        self.mft = p
        api = c_void_p()
        _call(self.mft, 0, (POINTER(GUID), POINTER(c_void_p)), byref(IID_ICodecAPI), byref(api))   # QueryInterface
        self.api = api
        # Low latency, no B-frames, a keyframe every few seconds, constant bitrate.
        self._set(MF_LOW_LATENCY, VT_BOOL, True)
        self._set(CODECAPI_B_PICTURES, VT_UI4, 0)
        self._set(CODECAPI_GOP_SIZE, VT_UI4, max(1, int(self.fps * gop_seconds)))
        self._set(CODECAPI_RATE_CONTROL, VT_UI4, 0)    # CBR
        self._set(CODECAPI_MEAN_BITRATE, VT_UI4, self.bitrate)

        out = self._type(H264)
        out_attrs = ((MF_MT_AVG_BITRATE, 32, self.bitrate), (MF_MT_MPEG2_PROFILE, 32, PROFILE_MAIN))
        for key, bits, val in out_attrs:
            self._attr(out, key, bits, val)
        _call(self.mft, 16, (c_uint32, c_void_p, c_uint32), 0, out, 0)     # SetOutputType (encoders: output first)
        _release(out)
        inp = self._type(NV12)
        _call(self.mft, 15, (c_uint32, c_void_p, c_uint32), 0, inp, 0)     # SetInputType
        _release(inp)
        _call(self.mft, 23, (c_uint32, c_void_p), MFT_MESSAGE_NOTIFY_BEGIN_STREAMING, None)
        _call(self.mft, 23, (c_uint32, c_void_p), MFT_MESSAGE_NOTIFY_START_OF_STREAM, None)
        info = StreamInfo()
        _call(self.mft, 7, (c_uint32, POINTER(StreamInfo)), 0, byref(info))   # GetOutputStreamInfo
        self.info = info

    def _type(self, subtype):
        mt = c_void_p()
        _mfplat.MFCreateMediaType(byref(mt))
        _call(mt, 24, (POINTER(GUID), POINTER(GUID)), byref(MF_MT_MAJOR_TYPE), byref(VIDEO))      # SetGUID
        _call(mt, 24, (POINTER(GUID), POINTER(GUID)), byref(MF_MT_SUBTYPE), byref(subtype))
        self._attr(mt, MF_MT_FRAME_SIZE, 64, (self.w << 32) | self.h)
        self._attr(mt, MF_MT_FRAME_RATE, 64, (self.fps << 32) | 1)
        self._attr(mt, MF_MT_PIXEL_ASPECT_RATIO, 64, (1 << 32) | 1)
        self._attr(mt, MF_MT_INTERLACE_MODE, 32, 2)     # progressive
        return mt

    @staticmethod
    def _attr(mt, key, bits, value):
        if bits == 32:
            _call(mt, 21, (POINTER(GUID), c_uint32), byref(key), value)
        else:
            _call(mt, 22, (POINTER(GUID), c_uint64), byref(key), value)

    def _set(self, key, vt, value):
        try:
            _call(self.api, 9, (POINTER(GUID), c_void_p), byref(key), ctypes.byref(_variant(vt, value)))   # SetValue
        except OSError as e:
            self.rejected.append((str(key), e))    # an old encoder without this setting: the defaults are fine

    def set_bitrate(self, bps):
        bps = int(max(200_000, bps))
        if bps != self.bitrate:
            self.bitrate = bps
            self._set(CODECAPI_MEAN_BITRATE, VT_UI4, bps)

    def force_keyframe(self):
        self._set(CODECAPI_FORCE_KEYFRAME, VT_UI4, 1)

    # -- one picture in, zero or more encoded pictures out
    def encode(self, bgra, width, height, force_key=False):
        """bgra: BGRA bytes of width x height (cropped to this encoder's even size if needed)."""
        if (width, height) != (self.w, self.h):
            if width < self.w or height < self.h:
                raise ValueError("picture size changed")
            im = Image.frombuffer("RGB", (width, height), bgra, "raw", "BGRX", 0, 1).crop((0, 0, self.w, self.h))
            bgra = im.tobytes("raw", "BGRX")
        if force_key:
            self.force_keyframe()
        nv12 = _rgb_to_nv12(bgra, self.w, self.h)
        self._feed(nv12)
        return self._drain()

    def _feed(self, nv12):
        buf, sample = c_void_p(), c_void_p()
        _mfplat.MFCreateMemoryBuffer(len(nv12), byref(buf))
        ptr = c_void_p()
        _call(buf, 3, (POINTER(c_void_p), c_void_p, c_void_p), byref(ptr), None, None)     # Lock
        ctypes.memmove(ptr, nv12, len(nv12))
        _call(buf, 4, ())                                                                  # Unlock
        _call(buf, 6, (c_uint32,), len(nv12))                                              # SetCurrentLength
        _mfplat.MFCreateSample(byref(sample))
        _call(sample, 42, (c_void_p,), buf)                                                # AddBuffer
        step = 10_000_000 // self.fps
        _call(sample, 36, (ctypes.c_int64,), self.n * step)                                # SetSampleTime
        _call(sample, 38, (ctypes.c_int64,), step)                                         # SetSampleDuration
        self.n += 1
        try:
            _call(self.mft, 24, (c_uint32, c_void_p, c_uint32), 0, sample, 0)              # ProcessInput
        finally:
            _release(sample)
            _release(buf)

    def _drain(self):
        frames = []
        while True:
            ob = OutputBuffer()
            own = None
            if not self.info.flags & MFT_OUTPUT_STREAM_PROVIDES_SAMPLES:
                own, buf = c_void_p(), c_void_p()
                _mfplat.MFCreateSample(byref(own))
                _mfplat.MFCreateMemoryBuffer(max(self.info.size, 1 << 20), byref(buf))
                _call(own, 42, (c_void_p,), buf)
                _release(buf)
                ob.sample = own.value
            status = c_uint32()
            hr = _call(self.mft, 25, (c_uint32, c_uint32, POINTER(OutputBuffer), POINTER(c_uint32)),
                       0, 1, byref(ob), byref(status), restype=c_long)
            sample = c_void_p(ob.sample)
            try:
                if hr == MF_E_TRANSFORM_NEED_MORE_INPUT:
                    return frames
                if hr == MF_E_TRANSFORM_STREAM_CHANGE:
                    continue
                if hr != S_OK:
                    raise OSError(hr, "H.264 encoder error 0x%08X" % (hr & 0xFFFFFFFF))
                data = self._read(sample)
                if data:
                    frames.append(self._finish(data))
            finally:
                _release(sample)
                if ob.events:
                    _release(c_void_p(ob.events))

    @staticmethod
    def _read(sample):
        buf = c_void_p()
        _call(sample, 41, (POINTER(c_void_p),), byref(buf))     # ConvertToContiguousBuffer
        try:
            ptr, cur = c_void_p(), c_uint32()
            _call(buf, 3, (POINTER(c_void_p), c_void_p, POINTER(c_uint32)), byref(ptr), None, byref(cur))
            try:
                return ctypes.string_at(ptr, cur.value)
            finally:
                _call(buf, 4, ())
        finally:
            _release(buf)

    def _finish(self, data):
        kinds = {k for k, _, _ in split_nals(data)}
        if 7 in kinds and 8 in kinds:
            self.header = b"".join(b"\x00\x00\x00\x01" + nal for k, _, nal in split_nals(data) if k in (7, 8))
        key = 5 in kinds
        if key and 7 not in kinds:
            data = (self.header or self._sequence_header()) + data
        return key, data

    def _sequence_header(self):
        """SPS+PPS from the encoder's output type, for keyframes that arrive without them."""
        mt = c_void_p()
        try:
            _call(self.mft, 18, (c_uint32, POINTER(c_void_p)), 0, byref(mt))        # GetOutputCurrentType
            size = c_uint32()
            _call(mt, 14, (POINTER(GUID), POINTER(c_uint32)), byref(MF_MT_MPEG_SEQUENCE_HEADER), byref(size))
            blob = ctypes.create_string_buffer(size.value)
            _call(mt, 15, (POINTER(GUID), c_char_p, c_uint32, POINTER(c_uint32)),
                  byref(MF_MT_MPEG_SEQUENCE_HEADER), blob, size.value, byref(size))
            self.header = blob.raw[:size.value]
        except OSError:
            self.header = b""
        finally:
            _release(mt)
        return self.header

    def close(self):
        for name in ("api", "mft"):
            obj = getattr(self, name, None)
            if obj:
                try:
                    _release(obj)
                except OSError:
                    pass
            setattr(self, name, None)

    def __del__(self):
        self.close()


def frame_header(is_key):
    """The one byte that follows each frame's length on the wire: 1 = keyframe, 0 = not."""
    return struct.pack("B", 1 if is_key else 0)


def available():
    """True if this PC can make an H.264 encoder (tries a small one)."""
    try:
        enc = H264Encoder(128, 128, fps=30, bitrate=500_000)
    except Exception:
        return False
    enc.close()
    return True
