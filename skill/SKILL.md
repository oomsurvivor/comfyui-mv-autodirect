---
name: mv-h3
description: Build a complete lip-sync music video with MiniMax H3 running locally in ComfyUI. Analyses the song, proposes a setting, writes a camera-angle library to match, asks for approval, then renders a finished MV with the scenes joined and the original track muxed on. Use when the user brings a song file, an isolated vocal stem and a character photo and wants a music video. Triggers on "/mv-h3", "make an MV with H3", "local MV", "ComfyUI lip-sync MV". Do NOT use for Higgsfield or Seedance MVs, and not for single-shot video.
---

# MV H3 — local lip-sync music videos

A four-node chain does everything: split the song into scenes, pick a camera
angle per scene, write the prompts, render, join, mux.

```
ScenePlanner ──► MV Auto Director ──► VisualDirector ──► MV Renderer Multi-Ref
  split scenes      pick angles         write prompts      render + join + mux
```

The first and third nodes come from `comfyui-minimax-h3-audio-T8`; the second
and fourth are in this repo.

Result:
`output/minimax_h3_t8_long_video/<chain_id>/assembled/..._master_audio.mp4` —
already joined and already carrying the original song. No ffmpeg needed.

Node constraints — the 500-character limit, the 21 banned words, the 6 silently
rewritten phrases — are in the repo README. Read it before writing angles.

---

## Step 1 — collect three files

| | |
|---|---|
| The full mixed song | muxed onto the delivery at the very end |
| **Isolated lead vocal** | drives scene boundaries and mouth shapes |
| A character photo | **one face only** |

No vocal stem means **stop and ask**. The mix cannot drive lip-sync.

Ask for the *lead* vocal, not a stem that still carries backing vocals. Backing
vocals humming while the lead rests count as singing, and the character opens
their mouth in the wrong places.

If the photo is a multi-panel character sheet, crop one front-facing panel.

Copy the image into `ComfyUI/input/`.

### Audio alignment

Both files must resolve to the **same sample count**. A 13 ms difference makes
the renderer refuse the job.

Two ways, depending on who is driving:

**Claude runs it** — use the helper script's `prep` command, which writes two
sample-exact `.wav` files into `ComfyUI/input/`.

**The user runs it in the GUI** — no command needed. The two `AudioCrop` nodes
handle it, as long as both carry the **same** `start_time` / `end_time` and
`end_time` is at least 1 second shorter than the shorter file.

Verified on a 2:43 song whose two files differed by 13 ms: `2:42` works, `2:43`
fails with `resolves to 3908 frames but requires 3907` — cropping past the real
length clamps each file to its own duration.

---

## Step 2 — measure the song, do not guess

Downmix to 8 kHz mono, compute RMS every half second, and read the energy curve
to find intro / verse / pre-chorus / chorus / breakdown / outro.

Ask the genre if it is unclear. Rap, ballad and EDM call for completely
different imagery.

---

## Step 3 — propose a setting, WAIT for approval

Write three strings:

- **global** — who, where, what surrounds them. Crowds are described here.
- **style** — image quality, grain, contrast
- **light** — source and direction, **locked to a fixed world position**

Present it in the user's language with reasons. Wait for approval.

### Setting by voice

The model takes the character from the **reference image**, not from the voice.
So the voice only decides mood and palette — the photo has to match already.

If the voice is ambiguous, **ask**. Guessing wrong tilts the whole video.

| Voice + genre | Settings that fit |
|---|---|
| **Male** rap / hip-hop | basketball court, parking garage, neon alley, night rooftop, warehouse, railway. Harsh sun or cold neon, hard contrast |
| **Male** ballad / rock | dark studio, empty road at dawn, old workshop, rain on glass, grey shore. One light source, half the face in shadow |
| **Female** pop / dance | sunlit rooftop, pastel room, evening beach, bright neon street. Even light, saturated, few hard shadows |
| **Female** ballad / R&B | raking window light, sunset field, piano room, sheer curtains. Soft light, gold hair rim, dissolved background |
| **Female** rap / hip-hop | the male list with the grit dialled down: cleaner alleys, less concrete, more colour |

Three rules for any voice:

- **Lock the light** to a fixed position so cuts do not flicker
- Low voices suit dark backgrounds and hard contrast; high voices suit bright
  and soft
- Sad songs want less `dyn` and more `static` and wide shots

---

## Step 4 — write the angle library

Write angles that fit the approved setting. 30–50 for a full song, 8–12 for a
short excerpt. Lean towards medium and full-body.

**Every `energy` tier must span all four shot sizes.** The picker filters by
tier first and balances sizes second, so a tier made only of close-ups produces
only close-ups no matter what the quota says. Learned the hard way: 10 angles
for a 4-scene excerpt, tier `lo` held only CU and MCU, and 2 of 4 scenes came
out as close-ups.

Below 6 scenes the size quota has no room to express itself — 12% of 4 scenes
is less than one scene. Do not judge a library on a short excerpt.

---

## Step 5 — show the scene plan, WAIT for approval

Show which scene gets which angle, how long it is, and whether it sings or
poses. **Only render after the user agrees.**

### Step 5b — hand over the paste blocks

Ask whether Claude should run it or the user will. If the user will, produce all
four blocks in the order the fields appear on the node:

| Order | Field | Required |
|---|---|---|
| 1 | `custom_global_prompt` | **yes** |
| 2 | `custom_angles_json` | no |
| 3 | `custom_light` | no, but worth it |
| 4 | `custom_pose` | no |

Also mention two switches that are not paste blocks: leave `busy_background` on
`auto`, and change `chain_id` for every new run.

The angle block runs to tens of thousands of characters. Write it to a file and
copy it to the clipboard rather than printing it. **Lint it first** — 500
characters, 21 banned words, 6 rewritten phrases — because a bad angle kills the
whole job.

### Always propose a singing/posing rhythm

Handing over three blocks without a rhythm leaves the user guessing. Always
propose a number and say what it is based on.

Run the planner once to get each scene's `start_seconds` and
`vocal_activity_ratio`, then pick posing scenes from four kinds of place:

| Where | Why |
|---|---|
| Intro, outro | no words yet, or none left |
| Breakdown / bridge | the song drops, the picture should drop with it |
| The scene right **after** a chorus payoff | a breath, so the line lands |
| The scene with the lowest `vocal_activity_ratio` | fewest words, lip-sync reads worst |

If the song has no clear structure, propose `pose_every` instead: 3–4 for dense
rap, 3 for pop, 2–3 for ballad, 2 when long backing-vocal passages exist.

---

## Step 6 — render

Change `chain_id` for every new run. Keep it the same and the renderer reads the
old manifest, skips everything and re-exports the old video in seconds — looking
exactly like success.

Interrupted? Run the same command again; it resumes at the unfinished scene.

---

## Step 7 — deliver

Over 100 MB, also produce a 960p copy for quick viewing and keep the original.

---

## Three ways to run it in the GUI

### A. Preview the scene plan — 1 second, no GPU

Bypass `MV Renderer Multi-Ref` with **Ctrl+B**, Run, read the table on
`PreviewAny`. Change `seed` and run again until it looks right.

Measured at 1.1 seconds. This replaces rendering test clips.

### B. Short excerpt

Change `end_time` on **both** `AudioCrop` nodes and use a new `chain_id`.

A short run costs **more** per scene — the 20 GB model reloads every scene at
~78 seconds a time. Full song: 1.57 min/scene. 20-second excerpt: 6.93. A
20-second test takes ~45 minutes; the whole song takes ~100. Prefer mode A.

### C. Full song

`start_time` `0:00`, `end_time` = song length minus 1–2 seconds, new `chain_id`.

| `max_scene_seconds` | Scenes in a 2:43 song | Wall time |
|---|---|---|
| 3.5 | ~59 | ~100 min |
| 6.0 | ~27 | ~60 min |

H3's training range is ~124–362 frames (5.2–15.1 s). Scenes of 2.5–3.5 s
(60–84 frames) sit **below** that and still look fine.

---

## Singing and posing

The character does not have to lip-sync every vocal moment. Real music videos
let the vocal run while the picture cuts to the singer posing, walking, staring
off.

Each scene is either `vocal_active` or `non_vocal`. For `non_vocal` the chain
writes *"relaxed closed mouth… no singing mouth shapes"* by itself.

**By vocal energy (automatic).** The `vocal_active_ratio` threshold. Measured on
one rap track: 0.12 gave 0% posing (rap is too dense), 0.70 gave ~37%, 0.85 gave
~54%. The right threshold is per-song — run the planner once and look at the
median `vocal_activity_ratio` first.

**By hand.** `pose_scenes` and `pose_every` on `MV Auto Director`. The scene
plan is hash-signed, so hand edits normally fail with `MV Scene Plan hash
mismatch`; this node edits and re-signs it, which is why `scene_plan` must route
**through** it rather than straight from the planner.

Posing content comes from 26 neutral built-in actions, deliberately written with
**no gaze lock** so the character can look away. For setting-specific actions,
paste into `custom_pose`. Every `perf` must contain `mouth closed and still`.

### Action after the line

Different mechanism. On a singing scene the chain keeps the angle's `perf`, so
write the action straight into it — no `custom_pose` needed:

> *"delivers the full line straight down the lens, then on the last beat a ball
> arrives into his hands and he rises and shoots it"*

Change the gaze lock to *"…while he is delivering the line"*, otherwise the model
makes him watch the camera while throwing.

---

## Crowds

`global_creative_prompt` is **not** word-filtered; `scene_directions_json` is.
Describe crowds in the global prompt and never in an angle.

Four layers are needed to stop extras lip-syncing, all of them:

1. turned away / never facing camera directly
2. softened by depth of field
3. `lips stay closed and still; none of them mouths, sings, raps or lip-syncs any word`
4. give them something else to do
5. the closer: `Only the lead performer in the foreground sings.`

Against duplicated faces:

> *"Every other person is a clearly different individual with a different face,
> build, skin tone, hairstyle and clothing from the lead performer, and nobody
> else in the shot shares his face or his outfit."*

Against duplicated objects: do not list several actions that each imply an
object (`dribbling, passing, shooting` produced three basketballs). Write one
sentence that locks the count.

---

## Small traps

- Errors from the director only appear in `/history/<prompt_id>`; the output
  folder is simply empty, which reads like an ffmpeg failure. Always check
  `status_str` after submitting.
- The file count in `candidates/` equals the scene count. `r00001` in a filename
  is **not** a retry counter.
- After a node gains or changes a field, saved workflows still hold the old
  value and fail with `Value not in list`.
