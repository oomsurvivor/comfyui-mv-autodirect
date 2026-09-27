# -*- coding: utf-8 -*-
"""MV Auto Director - writes scene_directions_json for MiniMax H3 MV VocalLock V3.

Sits between ScenePlanner and VisualDirector. Picks a camera angle for every
scene, with four anti-monotony rules:
  1. never reuse the same entry within 6 scenes
  2. never reuse the same camera move within 4 scenes
  3. never two consecutive scenes with the same motion class
  4. a weighted quota spreads CU/MCU/MS/WS across the video

The 51-angle library is derived from the "Higgsfield camera move library"
(65 moves) and its "Angle of view language bank", with the moves that break
lip-sync removed and the 21 words the H3 director rejects avoided throughout.
"""
import hashlib
import json
import re

from .data import LIB, PRESETS

# Target shot-size distribution. Close-ups get the LOWEST quota because the H3
# node is already biased towards them ("tongue visibility ... facial muscles").
SIZE_TARGET = {"CU": 0.12, "MCU": 0.24, "MS": 0.31, "WS": 0.33}

# Moves that get expensive over a busy background: long exposure, orbiting and
# body-mounted rigs all have to solve motion for EVERY object in frame. Paired
# with a wide shot and a crowd, render time rose ~10x (measured: 16.7 vs 1.7 min).
COSTLY_MOVES = {"LOW SHUTTER", "3D ROTATION", "SNORRICAM", "ARC LEFT", "ARC RIGHT"}

# Presets that contain a crowd -> busy background.
BUSY_PRESETS = {"court_crowd", "street_crowd"}

# Six phrases that T8's _safe_vocal_camera_direction REWRITES without telling you:
#   wide shot / full body shot / long shot -> "stable performance framing"
#   from behind -> "from a front three-quarter angle"
#   profile -> "three-quarter face view"
# => for a real wide shot write "Full body in frame" + "84 degree diagonal field
#    of view" plus an explicit geometric constraint.
REWRITTEN = re.compile(
    r"\b(?:(?:extreme )?wide shot|(?:full-body|full body|long) shot|"
    r"over-the-shoulder shot|from behind|profile(?: shot)?|eye-only shot)\b",
    re.IGNORECASE)

UNSAFE = re.compile(
    r"\b(?:mirror|reflection|projection|screen|poster|portrait|crowd|background people|"
    r"double exposure|picture-in-picture|clone|duplicate|ghost|microphone|mic|television|"
    r"phone|tablet|painting|photo|photograph)\b", re.IGNORECASE)


# How T8 signs the scene plan (read from h3_t8/mv_lipsync_advanced.py):
#   _canonical_json = json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False)
#   _hash = sha256(_canonical_json(plan_without_plan_hash)).hexdigest()
def _canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _plan_hash(plan):
    p = dict(plan)
    p.pop("plan_hash", None)
    return hashlib.sha256(_canonical_json(p).encode("utf-8")).hexdigest()


def _parse_indices(text, n):
    """'3,7,12' or '3-5,9' -> {3,4,5,7,9,12}. Out-of-range indices are dropped."""
    out = set()
    for part in str(text or "").replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                for i in range(int(a), int(b) + 1):
                    out.add(i)
            except ValueError:
                continue
        else:
            try:
                out.add(int(part))
            except ValueError:
                continue
    return {i for i in out if 0 <= i < n}


def _apply_pose_override(scene_plan, pose_scenes, pose_every, extra=()):
    """Force some scenes to non_vocal ("posing"), then re-sign the plan.

    pose_scenes: '3,7,12' or '3-5,9'. Takes precedence over pose_every.
    pose_every : N > 0 -> every Nth scene poses (scenes N-1, 2N-1, ...).
    extra      : indices forced on top of either, e.g. non-singing free scenes.
    Returns (new_plan, list_of_forced_indices).
    """
    scenes = scene_plan["scenes"]
    n = len(scenes)
    if pose_scenes.strip():
        forced = _parse_indices(pose_scenes, n)
    elif pose_every and pose_every > 0:
        forced = {i for i in range(n) if (i + 1) % pose_every == 0}
    else:
        forced = set()
    forced |= {i for i in extra if 0 <= i < n}
    if not forced:
        return scene_plan, []

    plan = json.loads(json.dumps(scene_plan))          # deep copy
    for i in sorted(forced):
        plan["scenes"][i]["performance_state"] = "non_vocal"
    plan["plan_hash"] = _plan_hash(plan)
    return plan, sorted(forced)


def _load_custom_pose(text):
    """Read this MV's own posing library from the JSON pasted into the node.

    Shape: [{"perf": "...", "emo": "..."}, ...]
    Put {"replace": true} first to REPLACE the 26 built-in poses; otherwise the
    new ones are appended.
    Returns (list_of_pairs, replaced).

    Use it to let the character do something that fits the setting during
    non-singing scenes - shoot a hoop, open a car door - instead of the
    26 deliberately neutral built-ins.
    """
    text = (text or "").strip()
    if not text:
        return [], False
    try:
        data = json.loads(text)
    except Exception as e:
        raise ValueError("custom_pose is not valid JSON: %s" % e)
    if not isinstance(data, list):
        raise ValueError("custom_pose must be a JSON array []")

    replace = bool(data and isinstance(data[0], dict) and data[0].get("replace"))
    items = [e for e in data if isinstance(e, dict) and not e.get("replace")]
    out = []
    for i, e in enumerate(items):
        miss = [k for k in ("perf", "emo") if k not in e]
        if miss:
            raise ValueError("pose %d is missing field(s) %s" % (i, miss))
        pair = []
        for k in ("perf", "emo"):
            v = str(e[k])
            if len(v) > 500:
                raise ValueError("pose %d: %s is %d characters, the limit is 500" % (i, k, len(v)))
            hit = UNSAFE.findall(v)
            if hit:
                raise ValueError("pose %d: %s contains banned word(s) %s"
                                 % (i, k, sorted(set(x.lower() for x in hit))))
            pair.append(v)
        out.append(tuple(pair))
    if replace and not out:
        raise ValueError("custom_pose sets replace=true but contains no poses")
    return out, replace


# Gaze. Every one of the 51 built-in angles locks the eyes on the lens twice:
# a shared sentence ("His eyes stay locked on the lens ... straight down the
# barrel") plus an angle-specific clause ("delivers to the lens", "gaze locked on
# camera"). gaze="varied" strips both from singing scenes and gives each scene a
# definite gaze instead - 45 degrees, fully side-on, eyes down, now and then the
# lens. Written as positive statements: a line like "never looks at the camera"
# still plants the idea of the camera, and a vague gaze drifts back to the lens.
# The word "profile" is avoided on purpose: T8 rewrites it to "three-quarter".
_LENS_CLAUSE = re.compile(
    r"\b(?:lens|camera|barrel|into it|leaving it|eye contact|eye-line|eyeline)\b", re.I)

GAZE_LINES = (
    "While he sings his head is turned about forty-five degrees to his left, eyes "
    "resting on a point beyond the edge of the frame.",
    "While he sings his head is turned a full ninety degrees to his right, his face "
    "seen side-on, lips and jaw moving clearly in outline.",
    "He sings with his eyes lowered and slightly to one side, lifting them to the "
    "distance on the strongest beat.",
    "While he sings his head is turned about forty-five degrees to his right, gaze "
    "travelling along the space beside him.",
    "He sings straight into the lens, eyes holding it for the whole line.",
    "While he sings his head is turned a full ninety degrees to his left, his face "
    "seen side-on, lips and jaw moving clearly in outline.",
)


def _free_gaze(perf, n, seed):
    """Drop every lens-directed clause from a built-in perf, add a set gaze."""
    kept = []
    for sent in re.split(r"(?<=\.)\s+", perf.strip()):
        parts = [p.strip() for p in sent.rstrip(".").split(",")]
        keep = [p for p in parts if p and not _LENS_CLAUSE.search(p)]
        if keep:
            kept.append(", ".join(keep) + ".")
    line = GAZE_LINES[(n * 7 + seed) % len(GAZE_LINES)]
    return " ".join(kept + [line])


# Free scenes: shots written like a plain Ultra Speed / Singularity prompt -
# no singer, two people, a car on its own, a camera orbit - inside the MV chain.
#
# T8 bakes a one-person lip-sync contract into every scene prompt and checks it
# is still there before rendering, so the plan cannot carry a different prompt.
# Instead this node writes a MARKER into the scene's camera field. The marker
# rides through the Director into the prompt text untouched (the camera rewriter
# only changes six known phrases), and MV Renderer Multi-Ref swaps the whole
# prompt for the free one just before it reaches the text encoder - after every
# T8 check has already passed.
#
# The marker carries a hash of the free prompt. Editing the prompt changes the
# marker, which changes T8's plan, which changes the resume contract - so an
# edited free scene is never silently reused from an old run.
FREE_MARKER = re.compile(r"\bFREESCENE\d{2,3}[0-9A-F]{8}\b")


def _load_free_scenes(text, n):
    """Parse free_scenes_json -> {index: {"marker", "prompt", "sing"}}.

    Shape: [{"scene": 3, "prompt": "...", "sing": false}, ...]
    scene is 0-based, same numbering as the preview table.
    sing=false (default): the scene stops lip-syncing and the renderer feeds the
    model silence instead of the vocal, so no singer is conjured for a shot that
    should have none. sing=true: the vocal stays; name <Audio 1> in the prompt.
    """
    text = (text or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except Exception as e:
        raise ValueError("free_scenes_json is not valid JSON: %s" % e)
    if not isinstance(data, list):
        raise ValueError("free_scenes_json must be a JSON array []")
    out = {}
    for k, e in enumerate(data):
        if not isinstance(e, dict):
            raise ValueError("free scene %d must be an object" % k)
        if "scene" not in e or "prompt" not in e:
            raise ValueError("free scene %d needs both 'scene' and 'prompt'" % k)
        try:
            i = int(e["scene"])
        except (TypeError, ValueError):
            raise ValueError("free scene %d: 'scene' must be a number" % k)
        if not 0 <= i < n:
            # Nearly always a mismatch between two fields that must be edited
            # together, and manual_boundaries_json is marked advanced so
            # ComfyUI hides it - easy to update one and miss the other.
            raise ValueError(
                "free scene %d points at scene %d, but this plan only has scenes 0-%d. "
                "free_scenes_json and the scene plan disagree: %d scenes need %d cut "
                "points in manual_boundaries_json on the ScenePlanner node, which "
                "ComfyUI HIDES because T8 marks it advanced. Update that field too, "
                "or reload the workflow from its .json file where both already match."
                % (k, i, n - 1, i + 1, i))
        if i in out:
            raise ValueError("scene %d is listed twice in free_scenes_json" % i)
        prompt = " ".join(str(e["prompt"]).split())
        if not prompt:
            raise ValueError("free scene %d: prompt is empty" % k)
        sing = bool(e.get("sing", False))
        digest = hashlib.sha256(("%s|%s" % (sing, prompt)).encode("utf-8")).hexdigest()
        out[i] = {"marker": "FREESCENE%02d%s" % (i, digest[:8].upper()),
                  "prompt": prompt, "sing": sing}
    return out


def _load_custom_angles(text):
    """Read a custom angle library from the JSON pasted into the node.

    Shape: [{"move":..,"size":"CU|MCU|MS|WS","energy":"lo|mid|hi",
             "motion":"static|slow|dyn","cam":..,"perf":..,"emo":..}, ...]
    Put {"replace": true} first to REPLACE the 51 built-in angles; otherwise
    the new ones are appended.
    Returns (list_of_angles, replaced).
    """
    text = (text or "").strip()
    if not text:
        return [], False
    try:
        data = json.loads(text)
    except Exception as e:
        raise ValueError("custom_angles_json is not valid JSON: %s" % e)
    if not isinstance(data, list):
        raise ValueError("custom_angles_json must be a JSON array []")

    replace = bool(data and isinstance(data[0], dict) and data[0].get("replace"))
    items = [e for e in data if isinstance(e, dict) and not e.get("replace")]
    need = ("move", "size", "energy", "motion", "cam", "perf", "emo")
    for i, e in enumerate(items):
        miss = [k for k in need if k not in e]
        if miss:
            raise ValueError("angle %d is missing field(s) %s" % (i, miss))
        if e["size"] not in SIZE_TARGET:
            raise ValueError("angle %d: size must be CU/MCU/MS/WS" % i)
        if e["motion"] not in ("static", "slow", "dyn"):
            raise ValueError("angle %d: motion must be static/slow/dyn" % i)
        if e["energy"] not in ("lo", "mid", "hi"):
            raise ValueError("angle %d: energy must be lo/mid/hi" % i)
        for k in ("cam", "perf", "emo"):
            v = str(e[k])
            if len(v) > 500:
                raise ValueError("angle %d: %s is %d characters, the limit is 500" % (i, k, len(v)))
            hit = UNSAFE.findall(v)
            if hit:
                raise ValueError("angle %d: %s contains word(s) H3 rejects %s"
                                 % (i, k, sorted(set(x.lower() for x in hit))))
            bad = REWRITTEN.findall(v)
            if bad:
                raise ValueError("angle %d: %s contains phrase(s) H3 silently rewrites %s - "
                                 "for a real wide shot write 'Full body in frame' + "
                                 "'84 degree diagonal field of view'"
                                 % (i, k, sorted(set(x.lower() for x in bad))))
    if not items:
        raise ValueError("custom_angles_json contains no angles")
    return items, replace


# Crowd words in the scene description. Found -> busy background -> avoid
# pairing expensive moves with a wide shot (measured: 16.7 min for one scene).
CROWD_WORDS = re.compile(
    r"\b(?:people|persons|players|crowd|crowds|passers-?by|bystanders?|onlookers?|"
    r"audience|spectators?|pedestrians?|dancers?|fans|teammates?|friends|"
    r"passengers?|commuters?|shoppers?|diners?|customers?|guests?|students?|"
    r"everyone|others|group of|a few (?:men|women|kids|guys))\b", re.IGNORECASE)

# Negations: may contain a crowd word but actually mean nobody is there.
SOLO_WORDS = re.compile(
    r"(?:nobody else (?:is )?(?:present|around|visible|in sight|in frame)|"
    r"no one else (?:is )?(?:present|around|visible|in sight)|nobody around|"
    r"completely alone|by himself|by herself|deserted)", re.IGNORECASE)


def _detect_busy(text):
    """Guess whether the setting has a crowd. Returns (result, reason)."""
    t = str(text or "")
    solo = SOLO_WORDS.findall(t)
    crowd = CROWD_WORDS.findall(t)
    if crowd and not solo:
        return True, "found %s" % sorted(set(x.lower() for x in crowd))[:3]
    if crowd and solo:
        return False, "found %s but also %s" % (
            sorted(set(x.lower() for x in crowd))[:2],
            sorted(set(x.lower() for x in solo))[:2])
    return False, "no crowd words found"


def _tier(score, lo, hi):
    return "lo" if score <= lo else ("hi" if score >= hi else "mid")


def _choose(scenes, seed=0, busy_bg=False, lib=None):
    LIB = lib if lib else globals()['LIB']
    idx_of = {id(e): i for i, e in enumerate(LIB)}
    scores = [s.get("audio_activity_score", 0.0) for s in scenes]
    lo = sorted(scores)[len(scores) // 3] if scores else 0
    hi = sorted(scores)[2 * len(scores) // 3] if scores else 1

    out, sizes, motions = [], [], []
    used, size_last, move_last, mot_last = {}, {}, {}, {}
    size_n = {k: 0 for k in ("CU", "MCU", "MS", "WS")}
    mot_n = {k: 0 for k in ("static", "slow", "dyn")}

    for n, sc in enumerate(scenes):
        t = _tier(sc.get("audio_activity_score", 0.0), lo, hi)
        pool = [e for e in LIB if e["energy"] == t] or list(LIB)
        for gap in (6, 4, 2, 0):
            c = [e for e in pool if n - used.get(idx_of[id(e)], -99) > gap]
            if c:
                pool = c
                break
        for gap in (4, 2, 1, 0):
            c = [e for e in pool if n - move_last.get(e["move"], -99) > gap]
            if c:
                pool = c
                break
        if motions:
            pool = [e for e in pool if e["motion"] != motions[-1]] or pool
        if sizes:
            pool = [e for e in pool if e["size"] != sizes[-1]] or pool
        # expensive moves must not meet a wide shot over a busy background
        if busy_bg:
            pool = [e for e in pool
                    if not (e["move"] in COSTLY_MOVES and e["size"] in ("WS", "MS"))] or pool
        pool.sort(key=lambda e: (size_n[e["size"]] / SIZE_TARGET[e["size"]],
                                 mot_n[e["motion"]],
                                 used.get(idx_of[id(e)], -99),
                                 (idx_of[id(e)] * 37 + n * 31 + seed) % 97))
        pick = pool[0]
        i = idx_of[id(pick)]
        used[i] = n
        size_last[pick["size"]] = n
        move_last[pick["move"]] = n
        mot_last[pick["motion"]] = n
        size_n[pick["size"]] += 1
        mot_n[pick["motion"]] += 1
        sizes.append(pick["size"])
        motions.append(pick["motion"])
        out.append(pick)
    return out


# Performance text for POSING scenes. The gaze lock is deliberately DROPPED so
# the character is free to look away. Written to be NEUTRAL, tied to no setting,
# so it is reusable across any MV. For setting-specific actions (shoot a hoop,
# hold a steering wheel, open a door) paste into custom_pose - see
# _load_custom_pose().
POSE = [
    # --- still / inward ---
    ("stands still and lets his gaze travel slowly across the space in front of him, from one side "
     "to the other, never settling on the lens, chest rising once with a long breath, mouth closed "
     "and still", "searching, unsettled"),
    ("stares off well past the camera with his eyes narrowed, chin lifted, weight back on one heel, "
     "mouth closed and still", "distant, thinking"),
    ("closes his eyes for a long beat with his head tipped slightly back, then opens them on "
     "something far off to the side, mouth closed and still", "spent, letting go"),
    ("holds completely still and lets only his eyes move, tracking something passing from his left "
     "to his right, mouth closed and still", "watchful, quiet"),
    ("lowers his head until his chin nearly touches his chest, holds it, then lifts it slowly with "
     "his eyes fixed somewhere off past the lens, mouth closed and still", "heavy, gathering"),
    # --- movement ---
    ("takes a few slow steps toward one side of the frame, weight rolling heel to toe, head turned "
     "away from the camera, mouth closed and still", "loose, in his own world"),
    ("turns a slow half circle on the spot, arms hanging, letting his eyes sweep the space around "
     "him and never cross the lens, mouth closed and still", "taking stock, unhurried"),
    ("walks a few paces then stops short, one shoulder dropping, and looks back the way he came, "
     "mouth closed and still", "second thoughts, unresolved"),
    ("paces two steps one way, pivots hard, and paces back, hands loose, eyes on the ground ahead "
     "of him, mouth closed and still", "caged, restless"),
    ("steps backward slowly with his weight low, eyes off to one side, mouth closed and still",
     "wary, giving ground"),
    # --- touching the space ---
    ("leans his shoulders back against a wall, arms folded, one knee bent, looking off to the side "
     "past the lens, mouth closed and still", "cool, unbothered"),
    ("rests both forearms on a railing in front of him and leans his weight into it, head down, "
     "then turning slowly to one side, mouth closed and still", "worn in, contemplative"),
    ("crouches and presses one palm flat to the ground, head bowed, holding the position, mouth "
     "closed and still", "grounded, collecting himself"),
    ("reaches up and grips something overhead with one hand, hanging his weight from it, chest "
     "open, looking off to the side, mouth closed and still", "stretched out, defiant"),
    # --- high energy ---
    ("rolls his shoulders loose then throws two sharp shadow punches into the empty air beside the "
     "lens and resets his stance, mouth closed and still", "charged, itching to move"),
    ("drops his head and snaps it up on the beat, shoulders driving hard, feet planted wide, eyes "
     "off to one side, mouth closed and still", "explosive, barely held"),
    ("spins once fast on his heel and lands square with his arms out, breathing hard, gaze off past "
     "the camera, mouth closed and still", "reckless, alive"),
    ("bounces on the balls of his feet, arms swinging loose in rhythm, head nodding to the beat, "
     "eyes down and away, mouth closed and still", "in the pocket, easy"),
    ("throws both arms wide and holds them there, head tipped back, chest open to the space above "
     "him, mouth closed and still", "triumphant, wide open"),
    # --- hands and face ---
    ("drags a slow hand back over his hair and lets it fall, gaze dropping then lifting away to one "
     "side, mouth closed and still", "restless, brooding"),
    ("rolls his shoulders back and tugs his collar straight, eyes down on his own hands then away "
     "to the side, mouth closed and still", "composed, self-possessed"),
    ("rubs a thumb slowly across his knuckles, eyes down on his own hands, jaw tight, mouth closed "
     "and still", "coiled, holding something in"),
    ("pulls the neck of his jacket up and turns his face a quarter away from the lens, eyes down, "
     "mouth closed and still", "closed off, guarded"),
    # --- low / seated / slumped ---
    ("sits down low with his forearms across his knees and his head hanging, then lifts his chin "
     "and looks off to one side, mouth closed and still", "worn down, honest"),
    ("sinks to a crouch, elbows on knees, hands clasped, staring at a point on the ground in front "
     "of him, mouth closed and still", "burnt out, thinking hard"),
    ("leans forward with both hands braced on his thighs, catching his breath, head low and turned "
     "away from the lens, mouth closed and still", "emptied out, recovering"),
]


class MVAutoDirector:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "scene_plan": ("H3_T8_MV_SCENE_PLAN",),
                "preset": (["custom"] + list(PRESETS), {"default": "custom",
                    "tooltip": "custom = read the paste-in fields below. The rest are 7 ready-made settings."}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFF}),
            },
            "optional": {
                "custom_global_prompt": ("STRING", {
                    "multiline": True, "default": "",
                    "tooltip": "Required when preset = custom. Who, where, and what surrounds "
                               "them. Describe crowds HERE and never in the angle library - "
                               "this field is not word-filtered, scene directions are."}),
                "pose_scenes": ("STRING", {
                    "default": "",
                    "tooltip": "Force these scenes to stop lip-syncing, e.g. '3,7,12' or '3-5,9'. "
                               "Works even on scenes full of vocal - the node re-signs the "
                               "hash-protected scene plan for you."}),
                "pose_every": ("INT", {
                    "default": 0, "min": 0, "max": 20,
                    "tooltip": "0 = off. N > 0 = every Nth scene becomes a posing scene. "
                               "Ignored when pose_scenes is filled in."}),
                "busy_background": (["auto", "on", "off"], {
                    "default": "auto",
                    "tooltip": "auto = the node reads your scene description and decides. "
                               "When on, it avoids pairing expensive moves (LOW SHUTTER, "
                               "3D ROTATION, SNORRICAM, ARC) with a wide shot - measured: the "
                               "wrong pairing cost 16.7 minutes for one scene instead of 1.7. "
                               "Only set on/off to override."}),
                "custom_angles_json": ("STRING", {
                    "multiline": True, "default": "",
                    "tooltip": "Your own camera-angle library as JSON. Empty = the 51 built-in "
                               "angles. Put {\"replace\": true} first in the array to replace "
                               "them instead of appending. The node checks the 500-character "
                               "limit, 21 banned words and 6 silently rewritten phrases."}),
                "custom_light": ("STRING", {
                    "multiline": True, "default": "",
                    "tooltip": "Empty = the preset's lighting. Lock the light source to a FIXED "
                               "position in the world so only the camera moves - otherwise the "
                               "light jumps at every cut."}),
                "custom_pose": ("STRING", {
                    "multiline": True, "default": "",
                    "tooltip": "Posing actions for non-singing scenes, as JSON: "
                               "[{\"perf\":..,\"emo\":..}, ...]. Empty = the 26 built-in neutral "
                               "poses. Put {\"replace\": true} first to replace them. Use this "
                               "to let the character do something that fits the setting - shoot "
                               "a hoop, open a car door - instead of leaning on a wall. Each "
                               "perf must contain 'mouth closed and still'."}),
                "free_scenes_json": ("STRING", {
                    "multiline": True, "default": "",
                    "tooltip": "Scenes rendered from your own full prompt, like the Ultra Speed "
                               "workflow - a car with nobody in it, two people, a camera orbit. "
                               "JSON: [{\"scene\": 3, \"prompt\": \"...\", \"sing\": false}]. "
                               "scene is 0-based as in the preview table. sing=false feeds the "
                               "model silence so no singer appears; the song is still laid over "
                               "the finished MV. Connect the free_scenes output to MV Renderer "
                               "Multi-Ref or these scenes render as ordinary lip-sync shots."}),
                "gaze": (["varied", "lens"], {
                    "default": "varied",
                    "tooltip": "varied = singing scenes stop staring at the camera: each one "
                               "gets its own gaze - 45 degrees, fully side-on at 90 degrees, eyes "
                               "down, now and then the lens - and the renderer lifts T8's "
                               "'front-facing or three-quarter only' rule. lens = the old "
                               "behaviour, eyes locked on the lens in every singing scene. "
                               "Only the 51 built-in angles are changed; your own custom angles "
                               "keep exactly what you wrote."}),
            },
        }

    RETURN_TYPES = ("H3_T8_MV_SCENE_PLAN", "STRING", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("scene_plan", "scene_directions_json", "global_creative_prompt",
                    "visual_style", "report", "free_scenes")
    FUNCTION = "run"
    CATEGORY = "MiniMaxH3/MV"
    DESCRIPTION = ("Picks a camera angle for every scene from a 51-entry library. "
                   "Sits between ScenePlanner and VisualDirector.")

    def run(self, scene_plan, preset, seed, pose_scenes="", pose_every=0,
            custom_global_prompt="", custom_light="", custom_angles_json="",
            busy_background="auto", custom_pose="", free_scenes_json="", gaze="varied"):
        gl = custom_global_prompt.strip()
        li = custom_light.strip()
        if preset == "custom":
            if not gl:
                raise ValueError(
                    "MV Auto Director: preset is 'custom' but custom_global_prompt is empty. "
                    "Paste a scene description there, or pick one of the built-in presets.")
            if not li:
                li = ("soft even daylight fixed in the same world position for every shot, "
                      "gentle rim along his hair and shoulder, no flat frontal key")
            busy, why = _detect_busy(gl)
        else:
            p = PRESETS[preset]
            gl = gl or p["global"]
            li = li or p["light"]
            busy, why = _detect_busy(gl)
            busy = busy or (preset in BUSY_PRESETS)
            if preset in BUSY_PRESETS:
                why = "preset %s" % preset
        extra, replace = _load_custom_angles(custom_angles_json)
        lib = extra if (extra and replace) else (LIB + extra if extra else LIB)
        xpose, xreplace = _load_custom_pose(custom_pose)
        poses = xpose if (xpose and xreplace) else (POSE + xpose if xpose else POSE)
        if busy_background == "on":
            busy, why = True, "forced on"
        elif busy_background == "off":
            busy, why = False, "forced off"
        free = _load_free_scenes(free_scenes_json, len(scene_plan["scenes"]))
        silent = [i for i, f in free.items() if not f["sing"]]
        scene_plan, forced = _apply_pose_override(scene_plan, pose_scenes, pose_every,
                                                  extra=silent)
        scenes = scene_plan["scenes"]
        picks = _choose(scenes, seed, busy_bg=busy, lib=lib)

        builtin = {id(e) for e in LIB}
        dirs, problems, npose = [], [], 0
        for i, e in enumerate(picks):
            perf, emo = e["perf"], e["emo"]
            if scenes[i].get("performance_state") == "non_vocal":
                perf, emo = poses[npose % len(poses)]
                npose += 1
            elif gaze == "varied" and id(e) in builtin:
                perf = _free_gaze(perf, i, seed)
            cam = e["cam"]
            if i in free:
                # Only the marker matters - the renderer replaces the whole prompt.
                cam = "%s locked frame" % free[i]["marker"]
            d = {"camera": cam, "lighting": li,
                 "performance": perf, "emotion": emo}
            for k, v in d.items():
                if len(v) > 500:
                    problems.append("scene %d: %s is %d characters" % (i, k, len(v)))
                hit = UNSAFE.findall(v)
                if hit:
                    problems.append("scene %d: %s contains banned word(s) %s"
                                    % (i, k, sorted(set(x.lower() for x in hit))))
            dirs.append(d)
        if problems:
            raise ValueError("MV Auto Director: " + "; ".join(problems[:5]))

        sizes, mots, moves = {}, {}, {}
        for e in picks:
            sizes[e["size"]] = sizes.get(e["size"], 0) + 1
            mots[e["motion"]] = mots.get(e["motion"], 0) + 1
            moves[e["move"]] = moves.get(e["move"], 0) + 1
        npv = sum(1 for sc in scenes if sc.get("performance_state") == "non_vocal")
        src = ("custom library, replaced" if (extra and replace)
               else ("51 built-in + %d custom" % len(extra)) if extra
               else "51 built-in angles")
        boi = "custom (pasted prompt)" if preset == "custom" else ("preset " + preset)
        lines = ["%d scenes | %d/%d entries used | library: %s"
                 % (len(picks), len({id(e) for e in picks}), len(lib), src),
                 "setting: %s | busy background: %s (%s)" % (boi, "YES" if busy else "no", why),
                 "shot sizes: " + json.dumps(sizes), "motion: " + json.dumps(mots),
                 "singing: %d | posing: %d%s" % (len(scenes) - npv, npv,
                     ("  (forced: " + ",".join(map(str, forced)) + ")") if forced else "")]
        lines.append("gaze: %s" % ("varied - 45 deg / side-on 90 deg / down / lens, T8 "
                                   "front-facing rule lifted by the renderer"
                                   if gaze == "varied" else "lens - locked on camera"))
        if free:
            lines.append("free scenes: %s" % ",".join(map(str, sorted(free))))
        if free or gaze == "varied":
            lines.append("-> connect free_scenes to MV Renderer Multi-Ref, or the renderer "
                         "cannot act on this")
        lines.append("")
        for i, (e, sc) in enumerate(zip(picks, scenes)):
            if i in free:
                lines.append("%3d  %6.2fs  %-18s %-4s %-6s %-8s %s"
                             % (i, sc.get("duration_seconds", 0), "FREE", "-", "-",
                                "free+sing" if free[i]["sing"] else "free",
                                free[i]["prompt"][:60] + ("..." if len(free[i]["prompt"]) > 60 else "")))
                continue
            tag = "POSING" if sc.get("performance_state") == "non_vocal" else "sing"
            lines.append("%3d  %6.2fs  %-18s %-4s %-6s %-8s %s"
                         % (i, sc.get("duration_seconds", 0), e["move"], e["size"],
                            e["motion"], tag, dirs[i]["emotion"]))
        # Everything the renderer must act on, carried on one wire.
        render_notes = {
            "gaze": gaze,
            "scenes": {f["marker"]: {"scene": i, "prompt": f["prompt"], "sing": f["sing"]}
                       for i, f in free.items()},
        }
        return (scene_plan, json.dumps(dirs, ensure_ascii=False), gl,
                "cinematic realism, realistic skin texture, filmic contrast, real grain",
                "\n".join(lines), json.dumps(render_notes, ensure_ascii=False))


from .renderer_multiref import MVRendererMultiRef
from .chain_reset import MVChainReset

NODE_CLASS_MAPPINGS = {
    "MVAutoDirector": MVAutoDirector,
    "MVRendererMultiRef": MVRendererMultiRef,
    "MVChainReset": MVChainReset,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "MVAutoDirector": "MV Auto Director (51 camera angles)",
    "MVRendererMultiRef": "MV Renderer Multi-Ref (9 images / 3 videos / 3 audios)",
    "MVChainReset": "MV Chain Reset (one folder, reused every run)",
}
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
