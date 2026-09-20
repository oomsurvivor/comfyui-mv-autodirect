# -*- coding: utf-8 -*-
"""Renderer cho chuoi MV nhung nhan NHIEU REF, giong wf Ultra Speed Singularity.

Van de: node Renderer cua T8 viet cung mot slot tham chieu duy nhat -
mv_lipsync_advanced.py dong 1649:

    {"ref_image_1": reference_image},

Trong khi ham dung ben duoi (conditioning.py:392) chap nhan toi 9 anh, 3 video
va 3 audio. Tam anh con lai bi bo phi.

Cach lam o day: KHONG sua file cua T8 (sua la mat khi ho cap nhat). Thay vao do
goi lai dung ham render cua T8, nhung trong luc no chay thi tam thoi boc
build_conditioning de chen them ref vao cai dict kia. Chay xong tra lai nguyen
trang.

De model THAT SU dung anh thu hai, prompt phai goi ten no bang the <Picture 2>.
He the nam o prompt_tags.py. Viet the trong custom_global_prompt cua node
MV Auto Director, no se chay xuong tung canh.
"""
import contextlib
import hashlib
import sys

MAX_IMAGES, MAX_VIDEOS, MAX_AUDIOS = 9, 3, 3

def _sampler_options():
    """Danh sach sampler/scheduler LAY TU T8, khong lay tu comfy.samplers.

    T8 tu them 'dual_clock_euler', 'native_flow', 'beta57' - ba cai nay khong
    co trong danh sach chuan. Lay nham la workflow cu bao
    'Value not in list' y het loi busy_background hom truoc.

    Do luoi vi luc pack nay nap thi T8 co the chua nap xong.
    """
    for name, mod in list(sys.modules.items()):
        if name.endswith("h3_t8.sampling") and hasattr(mod, "SAMPLER_OPTIONS"):
            return list(mod.SAMPLER_OPTIONS), list(mod.SCHEDULER_OPTIONS)
    try:
        import comfy.samplers as cs
        return (["dual_clock_euler"] + [n for n in cs.SAMPLER_NAMES if n != "dual_clock_euler"],
                ["native_flow", "beta57"]
                + [n for n in cs.SCHEDULER_NAMES if n not in ("native_flow", "beta57")])
    except Exception:
        return None, None


def _t8_module():
    """Tim module mv_lipsync_advanced cua T8 trong sys.modules.

    Khong import theo ten duoc vi thu muc pack co dau gach ngang. Nhung
    ComfyUI da nap no roi, nen chi viec do trong sys.modules.
    """
    for name, mod in list(sys.modules.items()):
        if name.endswith("h3_t8.mv_lipsync_advanced") and hasattr(
            mod, "run_local_mv_vocal_lock_visual_in_node_loop"
        ):
            return mod
    raise RuntimeError(
        "MV Renderer Multi-Ref: khong tim thay module mv_lipsync_advanced cua "
        "comfyui-minimax-h3-audio-T8. Pack do da duoc cai va nap chua?")


def _is_ref_image_dict(obj):
    return isinstance(obj, dict) and any(str(k).startswith("ref_image") for k in obj)


@contextlib.contextmanager
def _inject(mod, images, videos, video_audios, audios):
    """Boc build_conditioning trong pham vi module cua T8 de chen them ref."""
    original = mod.build_conditioning

    def wrapped(*args, **kwargs):
        args = list(args)
        # Tim vi tri dict ref_images. Do theo NOI DUNG chu khong theo so thu tu,
        # de T8 co doi chu ky ham thi van chay.
        at = None
        for i, a in enumerate(args):
            if _is_ref_image_dict(a):
                at = i
                break
        if at is None:
            if _is_ref_image_dict(kwargs.get("ref_images")):
                d = dict(kwargs["ref_images"])
                for n, img in enumerate(images, len(d) + 1):
                    d["ref_image_%d" % n] = img
                kwargs["ref_images"] = d
                if videos:
                    kwargs["ref_videos"] = {
                        "ref_video_%d" % n: v for n, v in enumerate(videos, 1)}
                if video_audios:
                    kwargs["ref_video_audios"] = dict(video_audios)
                if audios:
                    kwargs["ref_audios"] = {
                        "ref_audio_%d" % n: a for n, a in enumerate(audios, 1)}
            return original(*args, **kwargs)

        d = dict(args[at])
        for n, img in enumerate(images, len(d) + 1):
            d["ref_image_%d" % n] = img
        args[at] = d
        # Thu tu trong chu ky: ref_images, ref_videos, ref_video_audios, ref_audios.
        # Chuoi MV dang truyen None cho ca ba cai sau.
        if videos and at + 1 < len(args):
            args[at + 1] = {"ref_video_%d" % n: v for n, v in enumerate(videos, 1)}
        if video_audios and at + 2 < len(args):
            args[at + 2] = dict(video_audios)
        if audios and at + 3 < len(args):
            args[at + 3] = {"ref_audio_%d" % n: a for n, a in enumerate(audios, 1)}
        return original(*args, **kwargs)

    mod.build_conditioning = wrapped
    try:
        yield
    finally:
        mod.build_conditioning = original


def _fingerprint(items):
    """Dau van tay cua bo ref phu, de bao cho user biet no da doi."""
    h = hashlib.sha256()
    for it in items:
        if it is None:
            h.update(b"-")
        elif hasattr(it, "shape"):
            h.update(str(tuple(it.shape)).encode())
            try:
                h.update(str(float(it.flatten()[:64].sum())).encode())
            except Exception:
                pass
        elif isinstance(it, dict):
            h.update(str(sorted(it.keys())).encode())
        else:
            h.update(str(type(it)).encode())
    return h.hexdigest()[:12]


class MVRendererMultiRef:
    @classmethod
    def INPUT_TYPES(cls):
        samplers, schedulers = _sampler_options()
        req = {
            "model": ("MODEL",),
            "clip": ("CLIP",),
            "video_vae": ("VAE",),
            "audio_vae": ("VAE",),
            "reference_image": ("IMAGE", {
                "tooltip": "Anh nhan dang chinh. Day la <Picture 1> trong prompt."}),
            "full_song": ("AUDIO",),
            "vocal_lock_audio": ("AUDIO",),
            "mv_vocal_lock_prompt_plan": ("H3_T8_MV_VOCAL_LOCK_PROMPT_PLAN",),
            "chain_id": ("STRING", {"default": "my_mv_multiref"}),
            "width": ("INT", {"default": 1344, "min": 32, "max": 16384, "step": 32}),
            "height": ("INT", {"default": 768, "min": 32, "max": 16384, "step": 32}),
            "base_seed": ("INT", {"default": 123456789, "min": 0, "max": 0xFFFFFFFFFFFFFFFF}),
            "steps": ("INT", {"default": 4, "min": 1, "max": 1000,
                              "tooltip": "Ref2V Turbo v0.1 dung 4 NFE."}),
            "shift_video": ("FLOAT", {"default": 12.0, "min": 0.01, "max": 100.0, "step": 0.01}),
            "shift_audio": ("FLOAT", {"default": 3.0, "min": 0.01, "max": 100.0, "step": 0.01}),
            "sampler_name": ((samplers, {"default": "dual_clock_euler"}) if samplers
                             else ("STRING", {"default": "dual_clock_euler"})),
            "scheduler": ((schedulers, {"default": "beta"}) if schedulers
                          else ("STRING", {"default": "beta"})),
            "resume_existing": ("BOOLEAN", {"default": True}),
            "filename_prefix": ("STRING", {"default": "MV_OUT"}),
            "bit_depth": ([8, 10], {"default": 8}),
            "crf": ("INT", {"default": 18, "min": 0, "max": 51}),
            "model_id": ("STRING", {
                "default": "minimax_h3_ref2va+official_ref2v_turbo4_v0.1"}),
        }
        opt = {}
        for i in range(2, MAX_IMAGES + 1):
            opt["ref_image_%d" % i] = ("IMAGE", {
                "tooltip": "Anh tham chieu thu %d. Goi trong prompt bang <Picture %d>. "
                           "Dung cho trang phuc, boi canh, do vat." % (i, i)})
        for i in range(1, MAX_VIDEOS + 1):
            opt["ref_video_%d" % i] = ("IMAGE", {
                "tooltip": "Video tham chieu %d (chuoi IMAGE, toi thieu 5 frame). "
                           "Goi bang <Video %d>." % (i, i)})
            opt["ref_video_audio_%d" % i] = ("AUDIO", {
                "tooltip": "Tieng di kem ref_video_%d." % i})
        for i in range(1, MAX_AUDIOS + 1):
            opt["ref_audio_%d" % i] = ("AUDIO", {
                "tooltip": "Audio tham chieu %d. Goi bang <Audio %d>." % (i, i)})
        return {"required": req, "optional": opt}

    RETURN_TYPES = ("STRING", "STRING", "INT", "STRING", "STRING")
    RETURN_NAMES = ("video_path", "manifest_path", "completed_scenes", "status", "report")
    FUNCTION = "run"
    CATEGORY = "MiniMaxH3/MV"
    OUTPUT_NODE = True
    DESCRIPTION = ("Renderer chuoi MV nhung nhan toi 9 anh, 3 video, 3 audio tham chieu. "
                   "Prompt goi tung cai bang <Picture N> / <Video N> / <Audio N>.")

    def run(self, **kw):
        images, videos, audios, vauds = [], [], [], {}
        for i in range(2, MAX_IMAGES + 1):
            v = kw.pop("ref_image_%d" % i, None)
            if v is not None:
                images.append(v)
        for i in range(1, MAX_VIDEOS + 1):
            v = kw.pop("ref_video_%d" % i, None)
            a = kw.pop("ref_video_audio_%d" % i, None)
            if v is not None:
                videos.append(v)
                if a is not None:
                    vauds["ref_video_audio_%d" % len(videos)] = a
            elif a is not None:
                raise ValueError(
                    "MV Renderer Multi-Ref: da noi ref_video_audio_%d nhung "
                    "ref_video_%d con trong. Tieng phai di kem video." % (i, i))
        for i in range(1, MAX_AUDIOS + 1):
            v = kw.pop("ref_audio_%d" % i, None)
            if v is not None:
                audios.append(v)

        total_img = 1 + len(images)
        if total_img > MAX_IMAGES:
            raise ValueError("Toi da %d anh ke ca reference_image, dang co %d"
                             % (MAX_IMAGES, total_img))

        mod = _t8_module()
        fp = _fingerprint(images + videos + audios)

        with _inject(mod, images, videos, vauds, audios):
            video_path, manifest_path, completed, status, report = (
                mod.run_local_mv_vocal_lock_visual_in_node_loop(**kw))

        note = ("ref: %d anh (<Picture 1..%d>), %d video, %d audio | dau van tay bo ref phu: %s"
                % (total_img, total_img, len(videos), len(audios), fp))
        if images or videos or audios:
            note += ("\nLUU Y: bo ref phu KHONG nam trong hop dong resume. Doi ref ma giu "
                     "nguyen chain_id thi cac canh cu van duoc dung lai - phai doi chain_id.")
        return (video_path, manifest_path, completed, status, report + "\n" + note)
