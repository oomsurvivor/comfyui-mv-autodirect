# -*- coding: utf-8 -*-
"""MV Auto Director — sinh scene_directions_json cho MiniMax H3 MV VocalLock V3.

Cam thang giua ScenePlanner va VisualDirector. Tu chon goc quay cho tung canh
theo nang luong nhac, voi 4 luat chong nham chan:
  1. khong lap dung muc do trong 6 canh
  2. khong lap cung kieu chuyen dong may (move) trong 4 canh
  3. khong hai canh lien tiep cung nhip (tinh / cham / dong)
  4. han ngach co canh + nhip -> CU/MCU/MS/WS chia deu

Thu vien 51 goc dua tren "Higgsfield camera move library" (65 move) va
"Angle of view language bank" cua skill seedance-cinedance, da loc bo cac move
pha lip-sync va da tranh 21 tu bi H3 Director cam.
"""
import hashlib
import json
import re

from .data import LIB, PRESETS

# Ti le co canh mong muon. Can canh co han ngach THAP nhat vi node H3 von
# thien lech san ve phia do ("tongue visibility ... facial muscles").
SIZE_TARGET = {"CU": 0.12, "MCU": 0.24, "MS": 0.31, "WS": 0.33}

# Move DAT khi nen phuc tap: phoi sang dai / xoay quanh / may gan than deu phai
# tinh chuyen dong cho MOI vat the trong khung. Ghep voi khung rong + dam dong
# thi thoi gian render tang gan 10 lan (do duoc: 16,7 phut so voi 1,7 phut).
COSTLY_MOVES = {"LOW SHUTTER", "3D ROTATION", "SNORRICAM", "ARC LEFT", "ARC RIGHT"}

# Preset co dam dong -> nen phuc tap.
BUSY_PRESETS = {"court_crowd", "street_crowd"}

# Sau cum bi _safe_vocal_camera_direction cua pack T8 VIET LAI ngam:
#   wide shot / full body shot / long shot -> "stable performance framing"
#   from behind -> "from a front three-quarter angle"
#   profile -> "three-quarter face view"
# => canh rong phai viet "Full body in frame" + "84 degree diagonal field of view".
REWRITTEN = re.compile(
    r"\b(?:(?:extreme )?wide shot|(?:full-body|full body|long) shot|"
    r"over-the-shoulder shot|from behind|profile(?: shot)?|eye-only shot)\b",
    re.IGNORECASE)

UNSAFE = re.compile(
    r"\b(?:mirror|reflection|projection|screen|poster|portrait|crowd|background people|"
    r"double exposure|picture-in-picture|clone|duplicate|ghost|microphone|mic|television|"
    r"phone|tablet|painting|photo|photograph)\b", re.IGNORECASE)


# Cach pack T8 bam ke hoach canh (doc tu h3_t8/mv_lipsync_advanced.py):
#   _canonical_json = json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False)
#   _hash = sha256(_canonical_json(plan_khong_co_plan_hash)).hexdigest()
def _canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _plan_hash(plan):
    p = dict(plan)
    p.pop("plan_hash", None)
    return hashlib.sha256(_canonical_json(p).encode("utf-8")).hexdigest()


def _parse_indices(text, n):
    """'3,7,12' hoac '3-5,9' -> {3,4,5,7,9,12}. Bo qua chi so ngoai pham vi."""
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


def _apply_pose_override(scene_plan, pose_scenes, pose_every):
    """Ep mot so canh thanh non_vocal ('lam mau') roi ky lai ke hoach.

    pose_scenes: '3,7,12' hoac '3-5,9'. Uu tien hon pose_every.
    pose_every : N > 0 -> cu N canh thi 1 canh lam mau (canh N-1, 2N-1, ...).
    Tra ve (ke_hoach_moi, danh_sach_chi_so_da_ep).
    """
    scenes = scene_plan["scenes"]
    n = len(scenes)
    if pose_scenes.strip():
        forced = _parse_indices(pose_scenes, n)
    elif pose_every and pose_every > 0:
        forced = {i for i in range(n) if (i + 1) % pose_every == 0}
    else:
        return scene_plan, []
    if not forced:
        return scene_plan, []

    plan = json.loads(json.dumps(scene_plan))          # ban sao sach
    for i in sorted(forced):
        plan["scenes"][i]["performance_state"] = "non_vocal"
    plan["plan_hash"] = _plan_hash(plan)
    return plan, sorted(forced)


def _load_custom_pose(text):
    """Doc thu vien tu the LAM MAU rieng cho MV nay tu chuoi JSON dan vao node.

    Dang: [{"perf": "...", "emo": "..."}, ...]
    Dat {"replace": true} o phan tu dau de THAY han 26 tu the mac dinh,
    khong thi tu the moi duoc GOP them vao.
    Tra ve (danh_sach_cap, co_thay_han).

    Dung o day de nhan vat lam viec hop boi canh trong canh KHONG hat -
    nem bong vao ro, mo cua xe, dot thuoc - thay vi 26 tu the trung tinh.
    """
    text = (text or "").strip()
    if not text:
        return [], False
    try:
        data = json.loads(text)
    except Exception as e:
        raise ValueError("custom_pose khong phai JSON hop le: %s" % e)
    if not isinstance(data, list):
        raise ValueError("custom_pose phai la mot mang []")

    replace = bool(data and isinstance(data[0], dict) and data[0].get("replace"))
    items = [e for e in data if isinstance(e, dict) and not e.get("replace")]
    out = []
    for i, e in enumerate(items):
        miss = [k for k in ("perf", "emo") if k not in e]
        if miss:
            raise ValueError("tu the %d thieu truong %s" % (i, miss))
        pair = []
        for k in ("perf", "emo"):
            v = str(e[k])
            if len(v) > 500:
                raise ValueError("tu the %d: %s dai %d ky tu, toi da 500" % (i, k, len(v)))
            hit = UNSAFE.findall(v)
            if hit:
                raise ValueError("tu the %d: %s co tu cam %s"
                                 % (i, k, sorted(set(x.lower() for x in hit))))
            pair.append(v)
        out.append(tuple(pair))
    if replace and not out:
        raise ValueError("custom_pose dat replace=true nhung khong co tu the nao")
    return out, replace


def _load_custom_angles(text):
    """Doc thu vien goc rieng tu chuoi JSON dan vao node.

    Dang: [{"move":..,"size":"CU|MCU|MS|WS","energy":"lo|mid|hi",
            "motion":"static|slow|dyn","cam":..,"perf":..,"emo":..}, ...]
    Dat {"replace": true} o phan tu dau de THAY han 51 goc mac dinh,
    khong thi goc moi duoc GOP them vao.
    Tra ve (danh_sach_goc, co_thay_han).
    """
    text = (text or "").strip()
    if not text:
        return [], False
    try:
        data = json.loads(text)
    except Exception as e:
        raise ValueError("custom_angles_json khong phai JSON hop le: %s" % e)
    if not isinstance(data, list):
        raise ValueError("custom_angles_json phai la mot mang []")

    replace = bool(data and isinstance(data[0], dict) and data[0].get("replace"))
    items = [e for e in data if isinstance(e, dict) and not e.get("replace")]
    need = ("move", "size", "energy", "motion", "cam", "perf", "emo")
    for i, e in enumerate(items):
        miss = [k for k in need if k not in e]
        if miss:
            raise ValueError("goc %d thieu truong %s" % (i, miss))
        if e["size"] not in SIZE_TARGET:
            raise ValueError("goc %d: size phai la CU/MCU/MS/WS" % i)
        if e["motion"] not in ("static", "slow", "dyn"):
            raise ValueError("goc %d: motion phai la static/slow/dyn" % i)
        if e["energy"] not in ("lo", "mid", "hi"):
            raise ValueError("goc %d: energy phai la lo/mid/hi" % i)
        for k in ("cam", "perf", "emo"):
            v = str(e[k])
            if len(v) > 500:
                raise ValueError("goc %d: %s dai %d ky tu, toi da 500" % (i, k, len(v)))
            hit = UNSAFE.findall(v)
            if hit:
                raise ValueError("goc %d: %s co tu bi H3 cam %s"
                                 % (i, k, sorted(set(x.lower() for x in hit))))
            bad = REWRITTEN.findall(v)
            if bad:
                raise ValueError("goc %d: %s co cum bi node H3 viet lai ngam %s - "
                                 "canh rong phai viet 'Full body in frame' + "
                                 "'84 degree diagonal field of view'"
                                 % (i, k, sorted(set(x.lower() for x in bad))))
    if not items:
        raise ValueError("custom_angles_json khong co goc nao")
    return items, replace


# Tu chi dam dong trong mo ta boi canh. Thay -> nen phuc tap -> tranh ghep
# cac move dat voi khung rong (do duoc: ghep sai lam 1 canh mat 16,7 phut).
CROWD_WORDS = re.compile(
    r"\b(?:people|persons|players|crowd|crowds|passers-?by|bystanders?|onlookers?|"
    r"audience|spectators?|pedestrians?|dancers?|fans|teammates?|friends|"
    r"everyone|others|group of|a few (?:men|women|kids|guys))\b", re.IGNORECASE)

# Cum phu dinh: co the chua tu tren nhung y la KHONG co ai.
SOLO_WORDS = re.compile(
    r"(?:nobody else (?:is )?(?:present|around|visible|in sight|in frame)|"
    r"no one else (?:is )?(?:present|around|visible|in sight)|nobody around|"
    r"completely alone|by himself|by herself|deserted)", re.IGNORECASE)


def _detect_busy(text):
    """Doan xem boi canh co dam dong khong. Tra ve (ket_qua, ly_do)."""
    t = str(text or "")
    solo = SOLO_WORDS.findall(t)
    crowd = CROWD_WORDS.findall(t)
    if crowd and not solo:
        return True, "thay %s" % sorted(set(x.lower() for x in crowd))[:3]
    if crowd and solo:
        return False, "co %s nhung cung co %s" % (
            sorted(set(x.lower() for x in crowd))[:2],
            sorted(set(x.lower() for x in solo))[:2])
    return False, "khong thay tu chi dam dong"


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
        # move DAT khong duoc ghep voi khung rong khi nen phuc tap
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


# Dien xuat cho canh LAM MAU. Co y BO khoa anh mat - anh ta duoc nhin di cho khac.
# Co tinh viet TRUNG TINH, khong gan voi boi canh nao, de dung lai cho moi MV.
# Muon tu the rieng theo boi canh (nem bong, cam vo lang, mo cua...) thi dan
# vao o custom_pose - xem _load_custom_pose().
POSE = [
    # --- tinh / noi tam ---
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
    # --- di chuyen ---
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
    # --- cham vao khong gian ---
    ("leans his shoulders back against a wall, arms folded, one knee bent, looking off to the side "
     "past the lens, mouth closed and still", "cool, unbothered"),
    ("rests both forearms on a railing in front of him and leans his weight into it, head down, "
     "then turning slowly to one side, mouth closed and still", "worn in, contemplative"),
    ("crouches and presses one palm flat to the ground, head bowed, holding the position, mouth "
     "closed and still", "grounded, collecting himself"),
    ("reaches up and grips something overhead with one hand, hanging his weight from it, chest "
     "open, looking off to the side, mouth closed and still", "stretched out, defiant"),
    # --- nang luong cao ---
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
    # --- cu chi tay va mat ---
    ("drags a slow hand back over his hair and lets it fall, gaze dropping then lifting away to one "
     "side, mouth closed and still", "restless, brooding"),
    ("rolls his shoulders back and tugs his collar straight, eyes down on his own hands then away "
     "to the side, mouth closed and still", "composed, self-possessed"),
    ("rubs a thumb slowly across his knuckles, eyes down on his own hands, jaw tight, mouth closed "
     "and still", "coiled, holding something in"),
    ("pulls the neck of his jacket up and turns his face a quarter away from the lens, eyes down, "
     "mouth closed and still", "closed off, guarded"),
    # --- thap / ngoi / do nguoi ---
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
                    "tooltip": "custom = dung ba o dan ben duoi. Con lai la 7 boi canh lam san."}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFF}),
            },
            "optional": {
                "custom_global_prompt": ("STRING", {
                    "multiline": True, "default": "",
                    "tooltip": "De trong = dung preset. Dien vao de tu ta boi canh. "
                               "DAM DONG ta o day, khong bao gio ta trong scene_directions."}),
                "pose_scenes": ("STRING", {
                    "default": "",
                    "tooltip": "Ep canh nao thanh 'lam mau' (khong hat), vd '3,7,12' hoac '3-5,9'. "
                               "Dung duoc CA voi canh dang co vocal - node se ky lai ke hoach."}),
                "pose_every": ("INT", {
                    "default": 0, "min": 0, "max": 20,
                    "tooltip": "0 = tat. N > 0 = cu N canh thi 1 canh lam mau. "
                               "Bi bo qua neu da dien pose_scenes."}),
                "busy_background": (["auto", "on", "off"], {
                    "default": "auto",
                    "tooltip": "auto = node tu doc mo ta boi canh, thay tu chi dam dong thi bat. "
                               "Bat thi no tranh ghep move dat (LOW SHUTTER, 3D ROTATION, SNORRICAM, "
                               "ARC) voi khung rong - do duoc: ghep sai lam 1 canh mat 16,7 phut "
                               "thay vi 1,7. Chi dat on/off khi muon ep tay."}),
                "custom_angles_json": ("STRING", {
                    "multiline": True, "default": "",
                    "tooltip": "Thu vien goc rieng cho MV nay, dang JSON. De trong = dung 51 goc "
                               "mac dinh. Dat {\"replace\": true} o dau mang de THAY han. "
                               "Node tu kiem 500 ky tu, 21 tu cam va 6 cum bi H3 viet lai."}),
                "custom_light": ("STRING", {
                    "multiline": True, "default": "",
                    "tooltip": "De trong = dung anh sang cua preset. Phai khoa nguon sang "
                               "CO DINH trong the gioi, chi may di chuyen."}),
                "custom_pose": ("STRING", {
                    "multiline": True, "default": "",
                    "tooltip": "Tu the cho canh LAM MAU (canh khong hat), dang JSON: "
                               "[{\"perf\":..,\"emo\":..}, ...]. De trong = dung 26 tu the "
                               "trung tinh mac dinh. Dat {\"replace\": true} o dau mang de "
                               "THAY han. Dung de nhan vat lam viec hop boi canh - nem bong "
                               "vao ro, mo cua xe - thay vi tua tuong nhin xa xam."}),
            },
        }

    RETURN_TYPES = ("H3_T8_MV_SCENE_PLAN", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("scene_plan", "scene_directions_json", "global_creative_prompt",
                    "visual_style", "report")
    FUNCTION = "run"
    CATEGORY = "MiniMaxH3/MV"
    DESCRIPTION = ("Tu chon goc quay cho tung canh tu thu vien 51 cu may. "
                   "Cam giua ScenePlanner va VisualDirector.")

    def run(self, scene_plan, preset, seed, pose_scenes="", pose_every=0,
            custom_global_prompt="", custom_light="", custom_angles_json="",
            busy_background="auto", custom_pose=""):
        gl = custom_global_prompt.strip()
        li = custom_light.strip()
        if preset == "custom":
            if not gl:
                raise ValueError(
                    "MV Auto Director: preset dang la 'custom' nhung o custom_global_prompt "
                    "con trong. Dan mo ta boi canh vao do, hoac chon mot preset co san.")
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
            busy, why = True, "ep tay"
        elif busy_background == "off":
            busy, why = False, "ep tay"
        scene_plan, forced = _apply_pose_override(scene_plan, pose_scenes, pose_every)
        scenes = scene_plan["scenes"]
        picks = _choose(scenes, seed, busy_bg=busy, lib=lib)

        dirs, problems, npose = [], [], 0
        for i, e in enumerate(picks):
            perf, emo = e["perf"], e["emo"]
            if scenes[i].get("performance_state") == "non_vocal":
                perf, emo = poses[npose % len(poses)]
                npose += 1
            d = {"camera": e["cam"], "lighting": li,
                 "performance": perf, "emotion": emo}
            for k, v in d.items():
                if len(v) > 500:
                    problems.append("canh %d: %s dai %d ky tu" % (i, k, len(v)))
                hit = UNSAFE.findall(v)
                if hit:
                    problems.append("canh %d: %s co tu cam %s"
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
        src = ("goc rieng, thay han" if (extra and replace)
               else ("51 mac dinh + %d goc rieng" % len(extra)) if extra
               else "51 goc mac dinh")
        boi = "custom (prompt dan vao)" if preset == "custom" else ("preset " + preset)
        lines = ["%d canh | %d/%d muc | thu vien: %s" 
                 % (len(picks), len({id(e) for e in picks}), len(lib), src),
                 "boi canh: %s | nen phuc tap: %s (%s)" % (boi, "CO" if busy else "khong", why),
                 "co canh: " + json.dumps(sizes), "nhip: " + json.dumps(mots),
                 "hat: %d | lam mau: %d%s" % (len(scenes) - npv, npv,
                     ("  (ep tay: " + ",".join(map(str, forced)) + ")") if forced else ""), ""]
        for i, (e, sc) in enumerate(zip(picks, scenes)):
            tag = "LAM MAU" if sc.get("performance_state") == "non_vocal" else "hat"
            lines.append("%3d  %6.2fs  %-18s %-4s %-6s %-8s %s"
                         % (i, sc.get("duration_seconds", 0), e["move"], e["size"],
                            e["motion"], tag, dirs[i]["emotion"]))
        return (scene_plan, json.dumps(dirs, ensure_ascii=False), gl,
                "cinematic realism, realistic skin texture, filmic contrast, real grain",
                "\n".join(lines))


from .renderer_multiref import MVRendererMultiRef

NODE_CLASS_MAPPINGS = {
    "MVAutoDirector": MVAutoDirector,
    "MVRendererMultiRef": MVRendererMultiRef,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "MVAutoDirector": "MV Auto Director (51 goc)",
    "MVRendererMultiRef": "MV Renderer Multi-Ref (9 anh / 3 video / 3 audio)",
}
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
