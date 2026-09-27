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
import json
import re
import sys

MAX_IMAGES, MAX_VIDEOS, MAX_AUDIOS = 9, 3, 3

# Written into a scene's camera field by MV Auto Director (free_scenes_json).
FREE_MARKER = re.compile(r"\bFREESCENE\d{2,3}[0-9A-F]{8}\b")

# gaze="varied": T8 pins every singing scene to a frontal face with these two
# sentences (mv_lipsync_advanced.py, V3 route). Left in, they fight a side-on
# gaze and the model turns the head back to camera. Swapped at conditioning
# time, after T8 has already validated the plan with the original wording.
T8_FACE_RULES = (
    ("<Subject 1> stays in a medium close-up, front-facing or three-quarter face view; "
     "the full mouth is never cropped, covered, turned away, or motion-blurred.",
     "<Subject 1> stays in a medium close-up, his face shown from whatever angle the shot "
     "calls for, including fully side-on; the mouth is never cropped, covered, or "
     "motion-blurred."),
    ("The full face and unobstructed mouth remain visible for the entire shot; lips, jaw, "
     "tongue visibility, cheeks, and facial muscles articulate every audible phoneme",
     "The unobstructed mouth stays readable for the entire shot, and in a side-on view the "
     "lips and jaw read clearly in outline; lips, jaw, cheeks, and facial muscles "
     "articulate every audible phoneme"),
)

def _sampler_options():
    """Sampler/scheduler lists taken FROM T8, not from comfy.samplers.

    T8 adds 'dual_clock_euler', 'native_flow' and 'beta57', none of which exist
    in the stock lists. Building the menu from the stock lists makes every saved
    workflow fail with 'Value not in list'.

    Found by WHAT IT HAS, not by module name - ComfyUI renames custom-node
    modules between versions. Resolved lazily, because T8 may not have
    finished loading when this pack does.
    """
    for mod in list(sys.modules.values()):
        if mod is None:
            continue
        try:
            if hasattr(mod, "SAMPLER_OPTIONS") and hasattr(mod, "SCHEDULER_OPTIONS")                     and hasattr(mod, "DEFAULT_SAMPLER_NAME"):
                return list(mod.SAMPLER_OPTIONS), list(mod.SCHEDULER_OPTIONS)
        except Exception:
            continue
    try:
        import comfy.samplers as cs
        return (["dual_clock_euler"] + [n for n in cs.SAMPLER_NAMES if n != "dual_clock_euler"],
                ["native_flow", "beta57"]
                + [n for n in cs.SCHEDULER_NAMES if n not in ("native_flow", "beta57")])
    except Exception:
        return None, None


RENDER_FN = "run_local_mv_vocal_lock_visual_in_node_loop"


def _t8_module():
    """Find T8's mv_lipsync_advanced module among the already-loaded modules.

    Identify it by WHAT IT HAS, not by its name. The pack folder contains
    hyphens so it cannot be imported by name, and ComfyUI has changed how it
    names custom-node modules more than once - matching on the name broke on
    ComfyUI 0.36. The pair of attributes below is unique to this module.
    """
    loose = None
    for mod in list(sys.modules.values()):
        if mod is None:
            continue
        try:
            fn = getattr(mod, RENDER_FN, None)
            if fn is None or not hasattr(mod, "build_conditioning"):
                continue
            # Prefer the module that DEFINES the render function. Other modules
            # in the pack merely import it, and wrapping build_conditioning
            # there would have no effect on the one the renderer actually reads.
            if getattr(fn, "__module__", None) == getattr(mod, "__name__", None):
                return mod
            loose = loose or mod
        except Exception:
            continue
    if loose is not None:
        return loose

    # Fallback: load it straight off disk if ComfyUI has not imported it.
    import importlib.util
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for folder in sorted(os.listdir(here)):
        f = os.path.join(here, folder, "h3_t8", "mv_lipsync_advanced.py")
        if not os.path.isfile(f):
            continue
        spec = importlib.util.spec_from_file_location(
            "t8_mv_lipsync_advanced_%s" % abs(hash(f)), f)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception:
            continue
        if hasattr(mod, RENDER_FN):
            sys.modules[spec.name] = mod
            return mod

    raise RuntimeError(
        "MV Renderer Multi-Ref: could not find comfyui-minimax-h3-audio-T8. "
        "No loaded module exposes %s. Is that node pack installed, and did it "
        "import without errors? Check the ComfyUI startup log." % RENDER_FN)


def _is_ref_image_dict(obj):
    return isinstance(obj, dict) and any(str(k).startswith("ref_image") for k in obj)


def _is_audio(obj):
    return isinstance(obj, dict) and "waveform" in obj and "sample_rate" in obj


def _silence(audio):
    import torch
    return {"waveform": torch.zeros_like(audio["waveform"]),
            "sample_rate": audio["sample_rate"]}


def _apply_free(args, kwargs, free, applied):
    """Free scene: swap T8's whole prompt for the user's own one.

    The prompt is found by CONTENT - the string argument carrying a marker -
    not by position. A non-singing free scene also gets silence in place of
    the vocal, so the model is not pushed into inventing a singer for a shot
    that should have none. The finished MV still gets the full song muxed over
    it by T8, so nothing audible is lost.
    """
    slots = [("arg", i) for i, a in enumerate(args) if isinstance(a, str)]
    if isinstance(kwargs.get("prompt"), str):
        slots.append(("kw", "prompt"))
    for where, key in slots:
        text = args[key] if where == "arg" else kwargs[key]
        found = FREE_MARKER.search(text)
        if not found:
            continue
        spec = free.get(found.group(0))
        if spec is None:
            raise ValueError(
                "MV Renderer Multi-Ref: scene carries free-scene marker %s but no prompt for "
                "it arrived. Connect MV Auto Director's free_scenes output to this node's "
                "free_scenes input." % found.group(0))
        if where == "arg":
            args[key] = spec["prompt"]
        else:
            kwargs[key] = spec["prompt"]
        if not spec["sing"]:
            for i, a in enumerate(args):
                if _is_audio(a):
                    args[i] = _silence(a)
            for k, v in list(kwargs.items()):
                if _is_audio(v):
                    kwargs[k] = _silence(v)
        applied.append(spec["scene"])
        return True
    return False


def _relax_face_rules(args, kwargs, relaxed):
    """gaze='varied': let a singing scene show the face from any angle."""
    slots = [("arg", i) for i, a in enumerate(args) if isinstance(a, str)]
    if isinstance(kwargs.get("prompt"), str):
        slots.append(("kw", "prompt"))
    for where, key in slots:
        text = args[key] if where == "arg" else kwargs[key]
        new = text
        for old, rep in T8_FACE_RULES:
            new = new.replace(old, rep)
        if new != text:
            if where == "arg":
                args[key] = new
            else:
                kwargs[key] = new
            relaxed.append(1)
            return


_PICTURE_TAG = re.compile(r"<?\s*\b(?:picture|image)\s+(\d+)\b\s*>?", re.I)


def _named_pictures(args, kwargs):
    """Highest <Picture N> the scene's prompt names (1 if none).

    Extra reference images go only to scenes that name them. Without this, a
    second character's picture wired in for a cutaway would ride along into
    every lip-sync scene of the lead, unnamed, and could bleed into his face or
    put her in his shots. Numbering is kept: a scene naming <Picture 3> gets
    pictures 2 and 3. A setting that names <Picture 2> in custom_global_prompt
    puts the tag in every scene, so that usage is unchanged.
    """
    texts = [a for a in args if isinstance(a, str)]
    if isinstance(kwargs.get("prompt"), str):
        texts.append(kwargs["prompt"])
    prompt = max(texts, key=len) if texts else ""
    return max([1] + [int(n) for n in _PICTURE_TAG.findall(prompt)])


_VIDEO_TAG = re.compile(r"(<?\s*\bvideo\s+)(\d+)(\b\s*>?)", re.I)


def _prompt_slot(args, kwargs):
    """Where the scene prompt sits: the longest string argument, or kwargs."""
    if isinstance(kwargs.get("prompt"), str):
        return ("kw", "prompt")
    slots = [(len(a), i) for i, a in enumerate(args) if isinstance(a, str)]
    return ("arg", max(slots)[1]) if slots else (None, None)


def _scene_videos(args, kwargs, videos, video_audios):
    """Reference videos for THIS scene only: exactly the ones its prompt names.

    Unlike pictures, a scene gets only the videos it names - not every lower
    number too - because each clip is a different motion and an unnamed one
    would bleed its movement into the shot. They are renumbered 1..k in the
    order named and the prompt's tags are rewritten to match, so a cutaway that
    names only <Video 3> receives that clip as <Video 1>. A lip-sync scene that
    names none receives none, and skips the per-scene VAE encode of every clip.
    """
    where, key = _prompt_slot(args, kwargs)
    if where is None or not videos:
        return [], {}
    text = args[key] if where == "arg" else kwargs[key]
    named = sorted({int(n) for _, n, _ in _VIDEO_TAG.findall(text)
                    if 1 <= int(n) <= len(videos)})
    if not named:
        return [], {}
    remap = {old: new for new, old in enumerate(named, 1)}
    new_text = _VIDEO_TAG.sub(
        lambda m: m.group(1) + str(remap.get(int(m.group(2)), int(m.group(2)))) + m.group(3),
        text)
    if where == "arg":
        args[key] = new_text
    else:
        kwargs[key] = new_text
    scene_v = [videos[old - 1] for old in named]
    scene_a = {"ref_video_audio_%d" % new: video_audios["ref_video_audio_%d" % old]
               for old, new in remap.items() if "ref_video_audio_%d" % old in video_audios}
    return scene_v, scene_a


_STRICT_TAG = re.compile(r"<\s*(picture|image|video)\s+(\d+)\s*>", re.I)


def _free_tag_problems(free, n_pictures, n_videos):
    """Free-scene prompts that name media which is not wired in.

    T8 only checks tags inside build_conditioning, i.e. when that scene's turn
    comes - after every earlier scene has already rendered. Checked here instead,
    a bypassed loader stops the job before the GPU starts.
    """
    out = []
    for spec in sorted(free.values(), key=lambda s: s.get("scene", 0)):
        for kind, num in _STRICT_TAG.findall(spec.get("prompt", "")):
            n, kind = int(num), kind.lower()
            have = n_videos if kind == "video" else n_pictures
            if n > have:
                noun = "video" if kind == "video" else "picture"
                out.append("free scene %s names <%s %d> but %d %s%s connected"
                           % (spec.get("scene"), noun.title(), n, have, noun,
                              " is" if have == 1 else "s are"))
    return list(dict.fromkeys(out))


def _plan_markers(plan):
    """Every free-scene marker in the prompt plan, so a missing wire fails
    BEFORE the GPU starts rather than halfway through the chain."""
    out = set()
    for seg in (plan or {}).get("segments", []) or []:
        out.update(FREE_MARKER.findall(str(seg.get("prompt", ""))))
    return out


@contextlib.contextmanager
def _inject(mod, images, videos, video_audios, audios, free=None, applied=None,
            vary_gaze=False, relaxed=None, shown=None):
    """Wrap build_conditioning inside T8's module namespace to inject refs,
    swap in free-scene prompts, and lift the frontal-face rule."""
    original = mod.build_conditioning
    all_videos, all_video_audios = videos, video_audios

    def wrapped(*args, **kwargs):
        args = list(args)
        swapped = bool(free) and _apply_free(
            args, kwargs, free, applied if applied is not None else [])
        if vary_gaze and not swapped:
            _relax_face_rules(args, kwargs, relaxed if relaxed is not None else [])
        scene_images = images[:max(0, _named_pictures(args, kwargs) - 1)]
        videos, video_audios = _scene_videos(args, kwargs, all_videos, all_video_audios)
        if shown is not None:
            shown.append("%dp%s" % (1 + len(scene_images),
                                   "+%dv" % len(videos) if videos else ""))
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
                for n, img in enumerate(scene_images, len(d) + 1):
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
        for n, img in enumerate(scene_images, len(d) + 1):
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
        opt["free_scenes"] = ("STRING", {
            "forceInput": True,
            "tooltip": "From MV Auto Director's free_scenes output. Scenes listed there are "
                       "rendered from your own full prompt instead of the lip-sync template."})
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

        # T8 does not know this input - take it out before **kw reaches T8.
        free_text = (kw.pop("free_scenes", None) or "").strip()
        try:
            notes = json.loads(free_text) if free_text else {}
        except Exception as e:
            raise ValueError("MV Renderer Multi-Ref: free_scenes is not valid JSON: %s" % e)
        free = notes.get("scenes", {}) or {}
        vary_gaze = notes.get("gaze") == "varied"
        wanted = _plan_markers(kw.get("mv_vocal_lock_prompt_plan"))
        missing = sorted(wanted - set(free))
        if missing:
            raise ValueError(
                "MV Renderer Multi-Ref: the scene plan has %d free scene(s) but their prompts "
                "did not arrive (%s). Connect MV Auto Director's free_scenes output to this "
                "node's free_scenes input." % (len(missing), ", ".join(missing)))
        problems = _free_tag_problems({m: free[m] for m in wanted if m in free},
                                      1 + len(images), len(videos))
        if problems:
            raise ValueError(
                "MV Renderer Multi-Ref: checked before rendering - " + "; ".join(problems)
                + ". A reference loader is probably bypassed: select it, Ctrl+B to enable it "
                "and pick the file, or remove the tag from the free scene prompt.")

        mod = _t8_module()
        fp = _fingerprint(images + videos + audios)
        applied, relaxed, shown = [], [], []

        with _inject(mod, images, videos, vauds, audios, free=free, applied=applied,
                     vary_gaze=vary_gaze, relaxed=relaxed, shown=shown):
            video_path, manifest_path, completed, status, report = (
                mod.run_local_mv_vocal_lock_visual_in_node_loop(**kw))

        note = ("refs: %d images (<Picture 1..%d>), %d videos, %d audios | extra-ref fingerprint: %s"
                % (total_img, total_img, len(videos), len(audios), fp))
        if vary_gaze:
            note += ("\ngaze varied: T8 frontal-face rule lifted in %d rendered scene(s)"
                     % len(relaxed))
        if (images or videos) and shown:
            note += ("\nrefs per rendered scene, only those the prompt names "
                     "(p = pictures, v = videos): %s" % ",".join(shown))
        if wanted:
            done = sorted(set(applied))
            note += ("\nfree scenes: %d in plan, rendered this run: %s"
                     % (len(wanted), ",".join(map(str, done)) if done else "none"))
            if len(done) < len(wanted):
                note += (" (scenes already accepted in an earlier run are not re-rendered, "
                         "so they do not show here)")
        if images or videos or audios:
            note += ("\nNOTE: the extra references are NOT part of the resume contract. "
                     "Changing them while keeping the same chain_id silently reuses the old "
                     "scenes - change chain_id whenever this fingerprint changes.")
        return (video_path, manifest_path, completed, status, report + "\n" + note)
