"""Give every version of the prompt its own folder, automatically.

T8 keys all of its state on chain_id: output/minimax_h3_t8_long_video/<chain_id>.
Reusing a finished chain_id either re-exports the old video or, once anything in
the contract changes, refuses to run with "contains accepted segments from a
different contract". The usual fix is to invent a new name for every run, which
means remembering to do it and litters the output folder either way.

Default mode names the folder <chain_id>_<hash of the prompt plan AND the seed>,
reusing the hash the V3 Visual Director already computes. So:

  edit any prompt  -> new folder, renders fresh, the old video stays untouched
  change the seed  -> same, a fresh take beside the old one
  change nothing   -> same folder: an interrupted render resumes, a finished one
                      is returned in seconds and the report says so

No renaming, no contract errors, no lost takes. This node sits upstream of the
renderer's chain_id input, so ComfyUI's data dependency guarantees it runs first.

The seed lives HERE, not on the renderer: wire base_seed out of this node into
the renderer's base_seed. T8 counts base_seed in its resume contract, so a seed
changed on the renderer alone would collide with the old folder and be refused.

reset_before_run still wipes a folder on demand. The finished master is moved to
<state folder>/_finished/ first, so a good take is never lost by accident.
"""

import hashlib
import shutil
import time
from collections.abc import Mapping
from pathlib import Path

from .renderer_multiref import _t8_module


class MVChainReset:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "chain_id": ("STRING", {
                    "default": "mv",
                    "tooltip": "A name you never have to change. In per_prompt mode a short "
                               "hash of the prompt is appended automatically."}),
                "naming": (["per_prompt", "fixed"], {
                    "default": "per_prompt",
                    "tooltip": "per_prompt: the folder is named <chain_id>_<hash of the whole "
                               "prompt>. Edit any prompt and the next run is a NEW video in a "
                               "new folder, with the old one left untouched; run the same "
                               "prompt again and it resumes where it stopped. Needs prompt_plan "
                               "connected. fixed: the old behaviour, one folder reused."}),
                "base_seed": ("INT", {
                    "default": 424242, "min": 0, "max": 0xFFFFFFFFFFFFFFFF,
                    "tooltip": "Wire the base_seed output into MV Renderer Multi-Ref's base_seed "
                               "and set the seed HERE, not there. In per_prompt mode the seed is "
                               "part of the folder name, so changing it re-rolls the same prompt "
                               "into a new folder and keeps the old take."}),
                "reset_before_run": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "Wipe the folder before rendering. Leave OFF in per_prompt mode - "
                               "a changed prompt or a changed seed already gets its own folder, "
                               "and OFF lets an interrupted render continue."}),
                "keep_finished_master": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Move assembled/*_master_audio.mp4 to _finished/ before wiping."}),
            },
            "optional": {
                "prompt_plan": ("H3_T8_MV_VOCAL_LOCK_PROMPT_PLAN", {
                    "tooltip": "From the V3 Visual Director, the same wire that feeds the "
                               "renderer. Required by per_prompt naming."}),
            },
        }

    RETURN_TYPES = ("STRING", "INT", "STRING")
    RETURN_NAMES = ("chain_id", "base_seed", "report")
    FUNCTION = "run"
    CATEGORY = "MiniMaxH3/MV"
    DESCRIPTION = ("Names the chain folder after the prompt, so editing a prompt starts a new "
                   "video and leaves the old one alone. Wire chain_id into MV Renderer Multi-Ref.")

    @classmethod
    def IS_CHANGED(cls, **kw):
        # Never cache: the folder must be cleared on every single run.
        return float("nan")

    def run(self, chain_id, naming="per_prompt", base_seed=424242,
            reset_before_run=False, keep_finished_master=True, prompt_plan=None):
        mod = _t8_module()
        safe = mod.sanitize_chain_id(chain_id)
        log = []

        if naming == "per_prompt":
            # The V3 plan already carries a hash over every scene prompt, so an
            # edit anywhere - setting, light, an angle, a free scene - lands in a
            # different folder. Nothing is overwritten and nothing has to be renamed.
            digest = (prompt_plan or {}).get("prompt_plan_hash") if isinstance(prompt_plan, Mapping) else None
            if not digest:
                raise ValueError(
                    "MV Chain Reset: naming is 'per_prompt' but prompt_plan is not connected. "
                    "Wire the V3 Visual Director's mv_vocal_lock_prompt_plan output into this "
                    "node's prompt_plan input (the same wire that feeds the renderer), or set "
                    "naming to 'fixed'.")
            # Seed goes in too: same prompt with a new seed is a different take,
            # and T8 puts base_seed in its resume contract, so sharing a folder
            # would fail with "different contract" anyway.
            tag = hashlib.sha256(("%s|%d" % (digest, int(base_seed))).encode()).hexdigest()[:8]
            safe = mod.sanitize_chain_id("%s_%s" % (safe, tag))
            log.append("naming: per_prompt - this prompt+seed renders into its own folder")

        root = Path(mod.long_video_chain_root(safe))
        log += ["chain: %s" % safe, "folder: %s" % root]

        if naming == "per_prompt" and not reset_before_run:
            done = sorted((root / "assembled").glob("*_master_audio.mp4"))
            if done:
                log.append("ALREADY RENDERED - this exact prompt is finished, the run will "
                           "return %s in seconds. Edit a prompt for a new video, or turn "
                           "reset_before_run ON to render it again." % done[0].name)
            elif root.is_dir():
                log.append("partly rendered - the run continues where it stopped")
            else:
                log.append("new prompt - rendering from scratch")

        # Every return hands back `safe`, not `chain_id`: in per_prompt mode the
        # hash suffix IS the folder, so returning the bare name would send the
        # renderer somewhere else.
        if not reset_before_run:
            if naming == "fixed":
                log.append("reset OFF - stock T8 resume behaviour")
            return (safe, int(base_seed), "\n".join(log))

        if not root.is_dir():
            log.append("nothing to clear, this is a fresh chain")
            return (safe, int(base_seed), "\n".join(log))

        if keep_finished_master:
            masters = sorted((root / "assembled").glob("*_master_audio.mp4"))
            if masters:
                keep = root.parent / "_finished"
                keep.mkdir(parents=True, exist_ok=True)
                stamp = time.strftime("%Y%m%d_%H%M%S")
                for i, f in enumerate(masters):
                    dst = keep / ("%s_%s%s.mp4" % (safe, stamp, "" if i == 0 else "_%d" % i))
                    shutil.move(str(f), str(dst))
                    log.append("kept: _finished/%s" % dst.name)
            else:
                log.append("no finished master to keep")

        shutil.rmtree(root, ignore_errors=True)
        log.append("cleared" if not root.exists() else
                   "WARNING: could not fully clear (file in use?)")
        return (safe, int(base_seed), "\n".join(log))
