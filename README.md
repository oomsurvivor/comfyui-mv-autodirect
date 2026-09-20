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
shorter note on references beside the renderer. Read them before the first run;
they cover the mistakes that cost the most time.

---

## If you use Claude Code

`skill/SKILL.md` is a [Claude Code skill](https://docs.claude.com/en/docs/claude-code/skills).
Copy the `mv-h3` folder into `~/.claude/skills/` and type `/mv-h3`, and the
assistant walks the whole process: it measures the song's energy curve, proposes
a setting, writes an angle library to match, shows you the scene plan, and only
renders once you approve.

It is optional. Everything works by hand from the workflow alone.

---

## Models

Five files, about 41.3 GB:

| Download | Size | Put it in |
|---|---|---|
| [Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors](https://huggingface.co/WarmBloodAban/Minimax-h3_Singularity/resolve/main/Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors) | 19.5 GB | `models/diffusion_models` |
| [minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors) | 1.8 GB | `models/loras` |
| [qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors) | 14.6 GB | `models/text_encoders` |
| [minimax_h3_video_vae_fp16.safetensors](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_video_vae_fp16.safetensors) | 4.85 GB | `models/vae` |
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

### Why `busy_background` matters

Expensive moves (`LOW SHUTTER`, `3D ROTATION`, `SNORRICAM`, `ARC`) combined with
a wide shot over a busy background are brutally slow. Measured on one scene:
**16.7 minutes versus 1.7** for a normal shot. On `auto` the node scans your
description for crowd words and avoids that combination on its own.

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

### Things that will bite you

- **The extra references are not part of the resume contract.** Swapping an
  outfit image while keeping the same `chain_id` silently reuses the old
  scenes. The node prints a fingerprint of the extra reference set in `report`
  — when it changes, change `chain_id`.
- **More images of the same person means more consistency, not more variety.**
  For variety, give each image a distinct job and name it in the prompt.
- **Reference video is truncated to the scene length**, then aligned down to
  `17n+5` frames. A 2.9 s scene uses 56 frames (2.33 s) of your clip and
  discards the rest. Minimum 5 frames. It is also re-encoded through the VAE
  once per scene, so it costs time on every scene.
- **In an MV chain the driving vocal is already `<Audio 1>`**, so extra
  `ref_audio` slots are rarely worth it.

---

## Credits

Built on top of [`comfyui-minimax-h3-audio-T8`](https://github.com/T8mars/comfyui-minimax-h3-audio-T8)
by T8mars, which wraps MiniMax's H3 model. Those two do the actual work; this
repo only directs the camera and widens the reference slots.

Written with [Claude Code](https://claude.com/claude-code).

## License

MIT — see [LICENSE](LICENSE).
