---
name: stream-clip-studio
description: Set up a reusable Windows creator profile, import gameplay recordings, select and render vertical clips with a post-style layout, and schedule approved batches in the native TikTok Studio. Use for gameplay clips, initial creator setup, or verified TikTok scheduling.
---

# Stream Clip Studio

Run the commands from this skill directory. Read [README.md](README.md) for syntax and requirements.
The runner checks GitHub for updates at startup. If it prints an update notice, relay it to the user. Use `run.ps1 check-update` for a manual check and keep local profiles and media when replacing the skill files.

## Initial setup

Ask for the posting profile's display name, profile picture, @handle, and one Windows folder for originals and output clips. Ask for these four together. Run `setup.ps1 onboard` with the provided values or let the user answer the interactive prompts. The setup stores the profile outside this folder, then opens a dedicated Chromium window for manual TikTok login. Never ask for or store a password. Use the saved profile for later clips. If login or a security challenge needs intervention, let the user complete it in the browser. Setup does not upload or schedule anything.

## Import and cut

- Process a recording only after the user asks. Use `run.ps1 import` with a local video or a direct recording URL. It keeps the original at the root of the chosen media folder. Each original gets one subfolder named after its filename without extension for final MP4s.
- Run `analyze` with the requested duration range. Use the Gaming Clip candidates, timestamped transcript and frames to choose openings with action, surprise, euphoria or humor. Avoid idle stretches and overlapping picks. Use the requested clip count, duration and publish timing.
- Find the webcam during ingest. Inspect the crop before using it. Pass its source pixel rectangle to `render`; omit it if absent. Use the user's profile photo/name/@ at the top, enlarge the middle of the gameplay, keep the webcam above it, and credit the streamer's correct @ at the bottom. If the streamer handle is unknown and needed for the credit, ask.
- Keep the avatar, header text and streamer credit inside the mobile margins on the 1080 × 1920 canvas. The renderer has separate margins for UI text and the video; keep media large and leave room for mobile overlays. Use the larger 78 px watermark, still at 15% opacity.
- Use an engaging, grounded Portuguese title that doubles as the posting caption. Pick a moment within the clip for its first-frame cover and write a Portuguese cover hook of 2–5 words. The renderer builds the cover from the real frame, embeds it only as frame 0 and delivers an MP4. The profile's @ is the center watermark at 15% opacity.
- When dialogue has Portuguese and English, inspect the source and give the renderer only reliable Portuguese word timings via `--words`. Each word uses relative seconds and `language: pt`. English dialogue gets no burned-in caption. Check the first two frames, a captioned frame, and final resolution/duration/audio before delivery.

## Schedule

- Only schedule the exact files requested. Point the plan YAML at the relevant clips subfolder and preview the chosen video, fully dated schedule, timezone, caption, hashtags and privacy. Ask for a single approval of the batch; then run the approved plan hash. Keep a single window during the batch, and stop when it finishes.
- Never click immediate publishing instead of scheduling. The optional `confirm_pending_check` is for an explicitly approved Studio content-check dialog only, and it rereads schedule controls first. Keep it off unless approved for that batch. Let the user handle CAPTCHA/MFA.
- One safe recovery may replace a failed tab within the same Chromium window. After submission, repeat only read-only verification; reconcile ambiguous results and never resubmit them automatically. Report verified posts with links. Close the dedicated Chromium when the batch ends; do not reopen it after completion.
- Keep all login profiles, local settings, logs, databases, photos, video files, and generated plans outside this repository.
