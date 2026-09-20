# -*- coding: utf-8 -*-
"""MV chain renderer that accepts MANY references, like the Ultra Speed workflow.

The problem: T8's renderer hard-codes a single reference slot -
mv_lipsync_advanced.py line 1649:

    {"ref_image_1": reference_image},

while the builder underneath (conditioning.py:392) accepts up to 9 images,
3 videos and 3 audios. The other eight image slots go to waste.

The approach here: do NOT patch T8's files - a patch dies on their next
update. Instead call T8's own render function and, for the duration of that
call, wrap build_conditioning so it injects the extra references into that
dict. Everything is restored afterwards.

For the model to actually USE the second image, the prompt has to name it with
a <Picture 2> tag. That tag system lives in prompt_tags.py. Write the tag in
MV Auto Director's custom_global_prompt and it flows down into every scene.
"""
import contextlib
import hashlib
import sys

MAX_IMAGES, MAX_VIDEOS, MAX_AUDIOS = 9, 3, 3

def _sampler_options():
    """Sampler/scheduler lists taken FROM T8, not from comfy.samplers.

    T8 adds 'dual_clock_euler', 'native_flow' and 'beta57', none of which exist
    in the stock lists. Building the menu from the stock lists makes every saved
    workflow fail with 'Value not in list'.

    Resolved lazily, because T8 may not have finished loading when this pack does.
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
    """Find T8's mv_lipsync_advanced module in sys.modules.

    It cannot be imported by name because the pack folder contains a hyphen.
    ComfyUI has already loaded it, so just look it up.
    """
    for name, mod in list(sys.modules.items()):
        if name.endswith("h3_t8.mv_lipsync_advanced") and hasattr(
            mod, "run_local_mv_vocal_lock_visual_in_node_loop"
        ):
            return mod
    raise RuntimeError(
        "MV Renderer Multi-Ref: could not find the mv_lipsync_advanced module of "
        "comfyui-minimax-h3-audio-T8. Is that node pack installed and loaded?")


def _is_ref_image_dict(obj):
    return isinstance(obj, dict) and any(str(k).startswith("ref_image") for k in obj)


@contextlib.contextmanager
def _inject(mod, images, videos, video_audios, audios):
    """Wrap build_conditioning inside T8's module namespace to inject refs."""
    original = mod.build_conditioning

    def wrapped(*args, **kwargs):
        args = list(args)
        # Locate the ref_images dict by CONTENT rather than argument position,
        # so a signature change upstream does not break this.
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
        # Signature order: ref_images, ref_videos, ref_video_audios, ref_audios.
        # The MV chain currently passes None for the last three.
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
    """Fingerprint of the extra reference set, so the user notices it changed."""
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
                "tooltip": "Main identity image. This is <Picture 1> in the prompt."}),
            "full_song": ("AUDIO",),
            "vocal_lock_audio": ("AUDIO",),
            "mv_vocal_lock_prompt_plan": ("H3_T8_MV_VOCAL_LOCK_PROMPT_PLAN",),
            "chain_id": ("STRING", {"default": "my_mv_multiref"}),
            "width": ("INT", {"default": 1344, "min": 32, "max": 16384, "step": 32}),
            "height": ("INT", {"default": 768, "min": 32, "max": 16384, "step": 32}),
            "base_seed": ("INT", {"default": 123456789, "min": 0, "max": 0xFFFFFFFFFFFFFFFF}),
            "steps": ("INT", {"default": 4, "min": 1, "max": 1000,
                              "tooltip": "The official Ref2V Turbo v0.1 LoRA uses 4 NFE."}),
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
                "tooltip": "Reference image %d. Name it in the prompt as <Picture %d>. "
                           "Use it for an outfit, a location, an object." % (i, i)})
        for i in range(1, MAX_VIDEOS + 1):
            opt["ref_video_%d" % i] = ("IMAGE", {
                "tooltip": "Reference video %d (IMAGE sequence, at least 5 frames). "
                           "Name it as <Video %d>. Truncated to the scene length." % (i, i)})
            opt["ref_video_audio_%d" % i] = ("AUDIO", {
                "tooltip": "Soundtrack that belongs to ref_video_%d." % i})
        for i in range(1, MAX_AUDIOS + 1):
            opt["ref_audio_%d" % i] = ("AUDIO", {
                "tooltip": "Reference audio %d. Name it as <Audio %d>." % (i, i)})
        return {"required": req, "optional": opt}

    RETURN_TYPES = ("STRING", "STRING", "INT", "STRING", "STRING")
    RETURN_NAMES = ("video_path", "manifest_path", "completed_scenes", "status", "report")
    FUNCTION = "run"
    CATEGORY = "MiniMaxH3/MV"
    OUTPUT_NODE = True
    DESCRIPTION = ("MV chain renderer that accepts up to 9 images, 3 videos and 3 audios "
                   "as references. Name each one in the prompt with <Picture N> / "
                   "<Video N> / <Audio N>.")

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
                    "MV Renderer Multi-Ref: ref_video_audio_%d is connected but "
                    "ref_video_%d is empty. A soundtrack must accompany its video." % (i, i))
        for i in range(1, MAX_AUDIOS + 1):
            v = kw.pop("ref_audio_%d" % i, None)
            if v is not None:
                audios.append(v)

        total_img = 1 + len(images)
        if total_img > MAX_IMAGES:
            raise ValueError("At most %d images including reference_image, got %d"
                             % (MAX_IMAGES, total_img))

        mod = _t8_module()
        fp = _fingerprint(images + videos + audios)

        with _inject(mod, images, videos, vauds, audios):
            video_path, manifest_path, completed, status, report = (
                mod.run_local_mv_vocal_lock_visual_in_node_loop(**kw))

        note = ("refs: %d images (<Picture 1..%d>), %d videos, %d audios | extra-ref fingerprint: %s"
                % (total_img, total_img, len(videos), len(audios), fp))
        if images or videos or audios:
            note += ("\nNOTE: the extra references are NOT part of the resume contract. "
                     "Changing them while keeping the same chain_id silently reuses the old "
                     "scenes - change chain_id whenever this fingerprint changes.")
        return (video_path, manifest_path, completed, status, report + "\n" + note)
