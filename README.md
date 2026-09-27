# comfyui-mv-autodirect

Two ComfyUI nodes that fix the two biggest annoyances when building lip-sync
music videos with **MiniMax H3** locally:

1. **Every shot looks the same.** H3's MV chain hard-codes prompt text demanding
   *"tongue visibility"* and *"unobstructed mouth"*, which drags almost every
   scene into a close-up. `MV Auto Director` fights that with a library of
   camera angles and anti-repetition rules.
2. **Only one reference image.** The stock renderer accepts exactly one, even
   though the underlying builder supports nine images, three videos and three
   audios. `MV Renderer Multi-Ref` unlocks the rest.

Requires the [`comfyui-minimax-h3-audio-T8`](https://github.com/T8mars/comfyui-minimax-h3-audio-T8)
node pack — this repo extends it, it does not replace it.

---

## Install

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/oomsurvivor/comfyui-mv-autodirect
```

Restart ComfyUI. No extra Python packages.

### A ready-made workflow

`workflows/MV_H3.json` is a complete chain: load a song, its isolated vocal and
one photo, press Run, get a finished music video with the original track muxed
back on. Drop it into `ComfyUI/user/default/workflows/` or drag it onto the
canvas.

It carries two notes on the canvas — a **read me** covering install, the model
files, audio alignment, the three run modes and the `chain_id` trap, plus a
shorter note on references beside the renderer.

---

## If you use Claude Code

`skill/SKILL.md` is a [Claude Code skill](https://docs.claude.com/en/docs/claude-code/skills)
that writes the prompts for you. Optional — the workflow works by hand without it.

### Install

Copy it into your skills folder as `mv-h3/SKILL.md`:

```bash
mkdir -p ~/.claude/skills/mv-h3
cp skill/SKILL.md ~/.claude/skills/mv-h3/SKILL.md
```

On Windows that folder is `%USERPROFILE%\.claude\skills\mv-h3\`.

### Bring three things

| | Why |
|---|---|
| The **full mixed song** | muxed onto the delivery at the end |
| The **isolated lead vocal** | drives the scene splits and the mouth shapes |
| One **character photo**, single face | the person the video is about |

The vocal stem is not optional and it has to be the *lead* — a stem that still
carries backing vocals reads as singing while the lead rests, and the character
opens their mouth in the wrong places. Any stem splitter will do.

### Use it

```
/mv-h3
C:\music\song.mp3
C:\music\song (Lead Vocal).mp3
C:\pictures\character.png
```

What happens next:

1. **Measures the song** — downmixes it and reads the energy curve to find the
   intro, verses, choruses, the breakdown and the outro. It does not guess a
   setting from the title.
2. **Proposes a setting** and waits. You get a scene description, a lighting
   description and the reasoning, in your language. Nothing renders yet.
3. **Writes an angle library** to match that setting, and lints it against the
   500-character limit, the 21 banned words and the 6 rewritten phrases.
4. **Shows the scene plan** — every scene with its angle, length, and whether it
   sings or poses. Still nothing rendered.
5. **Hands you four blocks to paste** into `MV Auto Director`, in the order the
   fields appear on the node:

   | | Paste into | |
   |---|---|---|
   | 1 | `custom_global_prompt` | the setting — **required** |
   | 2 | `custom_angles_json` | the angle library, one long JSON line |
   | 3 | `custom_light` | the light source |
   | 4 | `custom_pose` | posing actions for non-singing scenes |

   It also proposes a `pose_scenes` or `pose_every` value and says what that is
   based on. Then set a fresh `chain_id` on the renderer and press Run.

   Or say the word and it drives ComfyUI for you instead.

Steps 2 and 4 are hard stops. Rendering a full song takes a long time, so the
skill makes you look at the free scene-plan preview first.

The angle block runs to tens of thousands of characters, so the skill puts it on
your clipboard rather than printing it — click the field and press Ctrl+V.

---

## Models

Five files, about 39.4 GB:

| Download | Size | Put it in |
|---|---|---|
| [Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors](https://huggingface.co/WarmBloodAban/Minimax-h3_Singularity/resolve/main/Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors) | 19.5 GB | `models/diffusion_models` |
| [minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors) | 1.8 GB | `models/loras` |
| [qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors) | 14.6 GB | `models/text_encoders` |
| [minimax_h3_video_vae_int8_convrot.safetensors](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_video_vae_int8_convrot.safetensors) | 2.95 GB | `models/vae` |
| [minimax_h3_audio_vae_fp32.safetensors](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_audio_vae_fp32.safetensors) | 0.56 GB | `models/vae` |

Direct links, no login needed. Four come from the official `Comfy-Org/MiniMax-H3`
repo; the diffusion model is WarmBloodAban's Singularity tune of it. On a smaller
card, swap the first one for [Singularity w4a8](https://huggingface.co/WarmBloodAban/Minimax-h3_Singularity/resolve/main/Minimax-h3_Singularity_ref2va_v1.3_Pruned_w4a8.safetensors)
(11 GB) and lower the resolution.

---

## Node 1 — MV Auto Director

Sits between `ScenePlanner` and `VisualDirector`:

```
ScenePlanner ──► MV Auto Director ──► VisualDirector ──► Renderer
```

The scene plan is hash-signed, so editing it by hand fails with
`MV Scene Plan hash mismatch`. This node edits it and **re-signs** it with
`sha256(canonical_json(plan))`.

### What it does

Picks a camera angle for every scene from a library of 51 built-in angles (or
your own), then writes `scene_directions_json` for the director.

Four anti-monotony rules run before a weighted quota balances shot sizes:

| Rule | Effect |
|---|---|
| No repeat of the same entry within 6 scenes | relaxes to 4, 2, 0 if the pool empties |
| No repeat of the same move within 4 scenes | relaxes to 2, 1, 0 |
| Never the same motion class twice in a row | static / slow / dyn |
| Never the same shot size twice in a row | CU / MCU / MS / WS |

Target distribution: `CU 12%`, `MCU 24%`, `MS 31%`, `WS 33%`.

### Inputs

| Field | |
|---|---|
| `preset` | `custom` (default) reads the paste fields below, or pick one of 7 built-in settings |
| `seed` | changes the selection without changing anything else |
| `custom_global_prompt` | scene description — **required** when `preset = custom` |
| `custom_angles_json` | your own angle library, JSON |
| `custom_light` | light source description |
| `custom_pose` | posing actions for non-singing scenes, JSON |
| `pose_scenes` | `"3,7,12"` or `"3-5,9"` — force those scenes to stop lip-syncing |
| `pose_every` | every Nth scene becomes a posing scene; ignored if `pose_scenes` is set |
| `busy_background` | `auto` reads your scene description and decides |
| `free_scenes_json` | scenes rendered from your own full prompt — see [Free scenes](#free-scenes) |
| `gaze` | `varied` (default) or `lens` — see below |

Connect the sixth output, `free_scenes`, to MV Renderer Multi-Ref. It carries
both the free scenes and the gaze mode.

### Gaze

Every one of the 51 built-in angles locks the eyes on the lens, twice over: a
shared sentence (*"His eyes stay locked on the lens … straight down the barrel"*)
plus an angle-specific clause (*"delivers to the lens"*). On top of that, T8's
template pins every singing scene to *"front-facing or three-quarter face view"*.

`gaze = varied` lifts both. The director strips every lens-directed clause from
the built-in angles and gives each singing scene a definite gaze instead,
rotating through six: 45° left, **fully side-on 90° right**, eyes lowered, 45°
right, straight into the lens, **fully side-on 90° left**. The renderer swaps
T8's frontal-face sentences for *"face shown from whatever angle the shot calls
for, including fully side-on … lips and jaw read clearly in outline"*.

Gaze lines are written as positive statements. *"Never looks at the camera"*
still plants the camera in the prompt, and a vague gaze drifts back to the lens.
The word `profile` is avoided — T8 rewrites it to `three-quarter face view`.

Only the built-in angles change. Your own `custom_angles_json` keeps exactly the
words you wrote. `gaze = lens` restores the old behaviour.

Untested at render time: T8 restricted singing to frontal views on purpose, and
lip-sync from a side-on view may be weaker. Check the first side-on scenes.

### Why `busy_background` matters

Expensive moves (`LOW SHUTTER`, `3D ROTATION`, `SNORRICAM`, `ARC`) combined with
a wide shot over a busy background are brutally slow — roughly ten times a
normal shot. On `auto` the node scans your description for crowd words and
avoids that combination on its own.

### Writing your own angles

```json
[{"replace": true},
 {"move": "DOLLY IN", "size": "MS", "energy": "mid", "motion": "slow",
  "cam": "47 degree diagonal field of view, normal lens, camera travelling forward from 6 to 4 metres...",
  "perf": "delivery tightening as the camera closes in...",
  "emo": "building, closing in"}]
```

`{"replace": true}` swaps out the built-in library; omit it to append.

Three constraints the node validates for you, because H3 silently drops jobs
that break them:

- **500 characters per field** — per field, not total.
- **21 banned words** scanned in every field including `emotion`:
  `mirror reflection projection screen poster portrait crowd "background people"
  "double exposure" picture-in-picture clone duplicate ghost microphone mic
  television phone tablet painting photo photograph`.
  Write `short telephoto lens`, never `portrait lens`. Write
  `left third of the frame`, never `screen-left`.
- **6 phrases the director silently rewrites**: `wide shot`, `full body shot`,
  `long shot`, `from behind`, `profile`, `over-the-shoulder shot`. Asking for a
  wide shot by name gets you `stable performance framing` instead. Use
  `84 degree diagonal field of view` plus an explicit geometric constraint:
  *"the top of his head and the soles of his feet are both inside the frame"*.

Give every `energy` tier all four shot sizes. The picker filters by tier first
and balances sizes second, so a tier made only of close-ups produces only
close-ups no matter what the quota says.

---

## Node 2 — MV Renderer Multi-Ref

Drop-in replacement for `MiniMaxH3LocalMVVocalLockVisualRendererV3T8Advanced`.
Same inputs, same widgets, plus 17 optional reference slots:

```
ref_image_2 … ref_image_9
ref_video_1 … ref_video_3   (+ ref_video_audio_1 … 3)
ref_audio_1 … ref_audio_3
```

It does **not** patch the T8 pack. It calls T8's own render function and
temporarily wraps `build_conditioning` to inject the extra references, then
restores it. The injection point is located by content, not by argument
position, so a signature change upstream will not break it.

### References only work if the prompt names them

| Tag | Points to |
|---|---|
| `<Picture 1>` | `reference_image` |
| `<Picture 2>` … `<Picture 9>` | `ref_image_2…9` |
| `<Video 1..3>` · `<Audio 1..3>` | reference video / audio |

Put the tags in `custom_global_prompt` on MV Auto Director:

> the man in `<Picture 1>` wearing the jacket from `<Picture 2>`, standing in
> the place shown in `<Picture 3>`

That splits **face / outfit / location** into separate images. Referencing a
picture you did not connect stops the job with a clear message rather than
guessing.

Numbering follows the **connected** slots, gaps closed: wire `ref_image_2` and
`ref_image_4` but leave `ref_image_3` empty, and the picture in slot 4 becomes
`<Picture 3>`. Fill the slots in order.

Extra pictures go **only to the scenes that name them**. For each scene the
renderer finds the highest `<Picture N>` in its prompt and passes pictures 2…N,
keeping the numbering; a scene that names none gets only `<Picture 1>`. A second
character wired in for a few cutaways therefore stays out of the lead's
lip-sync scenes. Naming a picture in `custom_global_prompt` puts the tag in
every scene, so that usage works as before. `report` lists how many pictures
each rendered scene received.

### Things that will bite you

- **The extra references are not part of the resume contract.** Swapping an
  outfit image while keeping the same `chain_id` silently reuses the old
  scenes. The node prints a fingerprint of the extra reference set in `report`
  — when it changes, change `chain_id`, or let MV Chain Reset clear the folder.
- **More images of the same person means more consistency, not more variety.**
  For variety, give each image a distinct job and name it in the prompt.
- **Reference video is truncated to the scene's render length**, then aligned
  down to `17n+5` frames. Every scene renders at least 124 frames and is trimmed
  afterwards, so a 2.9 s scene uses the first 124 frames (5.2 s) of your clip and
  discards the rest. Every scene sees the **same opening** of the clip — it is
  not time-aligned with the song. Minimum 5 frames. It is also re-encoded through the VAE
  once per scene, so it costs time on every scene.
- **In an MV chain the driving vocal is already `<Audio 1>`**, so extra
  `ref_audio` slots are rarely worth it.

### Free scenes

Every singing scene T8 builds carries a one-person contract — *"Exactly one
visible person and exactly one visible human face exist in the entire frame"* —
and T8 checks it is still there before rendering. No switch turns it off. So an
MV chain could never show a car on its own, a second character, or a shot that
circles an object.

Free scenes can. You write a whole prompt, the way you would for the Ultra Speed
workflow, and that prompt is what the model receives for that scene:

```json
[
  {"scene": 3, "sing": false, "prompt": "Night, a wide avenue after rain ... A low red supercar sweeps past ... No person and no human face appear anywhere in the frame at any moment."},
  {"scene": 7, "sing": false, "prompt": "... The woman from <Picture 2> leans against the car ... The camera circles slowly around the front of the car ..."}
]
```

| Field | |
|---|---|
| `scene` | 0-based, the same numbers as the preview table |
| `prompt` | the complete prompt — not word-filtered, no 500-character limit |
| `sing` | default `false`: the scene stops lip-syncing and the model is fed silence instead of the vocal, so it is not pushed into inventing a singer. `true` keeps the vocal; name `<Audio 1>` in the prompt |

How it works, without touching T8:

1. MV Auto Director writes a marker such as `FREESCENE0110F40AB1` into that
   scene's camera field. It passes through the director untouched.
2. T8 validates the plan with its original prompt — contract intact.
3. The renderer, wrapping `build_conditioning`, sees the marker and swaps in
   your prompt just before the text encoder.

The marker carries a hash of your prompt. Edit the prompt and the marker changes,
the plan changes, the resume contract changes — an edited free scene is never
silently reused from an old run. Forget the `free_scenes` wire and the renderer
stops **before** the GPU starts, naming the missing scenes.

Your prompt replaces T8's entirely, so it has to carry everything: the setting,
the light, and a `<Picture N>` tag for every person in it. Copy the MV's own
setting and light sentences in, or the cutaway will not match the colour of the
scenes around it.

To get a 1–2 second cutaway, set `manual_boundaries_json` on the scene planner
(it replaces automatic splitting, so list every cut), preview to read the scene
numbers, then list them here. Short scenes are not cheaper: the planner renders
every scene at least 124 frames and trims afterwards.

Untested at render time: a scene with no person at all. The model is built for
a performer; silence in place of the vocal plus an explicit *"No person …
appears"* is meant to hold it, but check the first render.

---

## Node 3 — MV Chain Reset

T8 keeps every run's state in `output/minimax_h3_t8_long_video/<chain_id>/`.
Reuse a finished `chain_id` and you get either the old video back in zero
seconds, or `contains accepted segments from a different contract` if anything
changed. The usual fix — a new name every run — litters the output folder.

MV Chain Reset feeds `chain_id` to the renderer, so ComfyUI's data dependency
guarantees it runs first. Its default `naming` mode, **`per_prompt`**, appends
the `prompt_plan_hash` the V3 Visual Director already computes:

| | |
|---|---|
| Edit any prompt | a **new** folder, rendered from scratch, the old video untouched |
| Re-run after an interruption | continues where it stopped |
| Re-run something already finished | returns it in seconds, and the report says `ALREADY RENDERED` |

No names to invent, no contract errors, no takes lost. Wire the Visual
Director's `mv_vocal_lock_prompt_plan` into this node's `prompt_plan` input —
the same wire that feeds the renderer. Without it the node stops with a clear
message rather than silently dropping the hash.

The seed is part of the hash too, so a new seed is a new take beside the old
one. Set it **on this node** and wire the `base_seed` output into the renderer's
`base_seed` input: T8 counts that seed in its resume contract, so a seed changed
on the renderer alone would collide with the existing folder and be refused.

`reset_before_run` still wipes a folder on demand; the finished master is moved
to `_finished/<name>_<timestamp>.mp4` first.

`naming: fixed` restores the old behaviour: one folder, reused.

---

## Credits

Built on top of [`comfyui-minimax-h3-audio-T8`](https://github.com/T8mars/comfyui-minimax-h3-audio-T8)
by T8mars, which wraps MiniMax's H3 model. Those two do the actual work; this
repo only directs the camera and widens the reference slots.

Written with [Claude Code](https://claude.com/claude-code).

## License

MIT — see [LICENSE](LICENSE).
