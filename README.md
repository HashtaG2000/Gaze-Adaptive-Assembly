# Gaze-Adaptive Assembly Instructions

This is a research prototype for a lab study on step-by-step Duplo assembly instructions. A participant works through the steps while a GazePoint GP3 eye tracker records where they look and a USB camera photographs the board after every step. After each repetition, the participant rates the layout and says what they want changed. An LLM then turns that request into a new instruction version: it decides which steps to combine and rewrites the step texts. The participant tries the new version in a **trial run**, which does not count as a repetition, and then keeps it or reverts.

There are two windows:

| Window | Who | What |
|---|---|---|
| **Participant window** | participant (tracked monitor, fullscreen) | intake, eye-tracker check, repetitions, feedback, trial runs and the final questionnaire |
| **Workspace** | experimenter / study designer (second monitor) | a sidebar with **Live**, **Recordings**, **Enrichments**, **Visualizations** and **Downloads** |

The app runs in two study conditions:
- `gaze`: the LLM gets gaze metrics and timing.
- `time_only`: the LLM gets timing only; gaze fields are removed before sending.

## Download (no Python needed)

1. Open the repository's **Releases** page on GitHub and download `GazeAdaptiveStudy-<version>-windows.zip`.
2. Unzip it anywhere (e.g. `C:\GazeAdaptiveStudy`).
3. Run `GazeAdaptiveStudy.exe`. `config.yaml` and `data\` next to it stay editable; sessions, logs and
   exports are written into the same folder.

Windows 10/11, 64-bit. The GP3 eye tracker needs Gazepoint Control (Windows). For the Gemini LLM set
`GEMINI_API_KEY`; Ollama and the mock options need no key.

**For maintainers:** pushing a version tag builds and publishes a release automatically
(`.github/workflows/release.yml`: tests → PyInstaller → the app's own `--check` → zip):

```powershell
git tag v2.1.0
git push origin v2.1.0
```

Participant data (`sessions/`, `logs/`, `exports/`) is excluded by `.gitignore` and must never be committed.

## 1. Setup from source (Windows)

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m app.main --check      # checks config, content, media, references, rules and survey
```

### LLMs

The app makes two kinds of LLM call:
- **Layout:** analysis and generation of new instruction versions.
- **Step checker:** comparing each camera photo with the step's reference image.

Both are chosen in Workspace → Live → pre-flight.

| Option | Needs | Notes |
|---|---|---|
| `mock` | nothing | Deterministic stand-in for tests. For layouts it follows fixed rules. For photos it compares pixels (an 8×8 block comparison) and does not understand the assembly. |
| `ollama` | [Ollama](https://ollama.com) running locally | No API key, and the data stays on the lab PC. Layout model: `llm.ollama.model`, e.g. `ollama pull qwen2.5:7b`. Vision model: `verification.ollama_model`; see the list below. |
| `gemini` | environment variable `GEMINI_API_KEY` | Set it with `setx GEMINI_API_KEY "<key>"`, then open a new terminal. The key is read only from the environment. The free tier of Google AI Studio has rate limits, and free-tier inputs may be used by Google, so check this against your ethics approval. |

**Free vision models for the step checker:**
- **Gemini 2.5 Flash** (`verification.gemini_model`): the most accurate of these.
- **Ollama, local:**
  - `qwen2.5vl:7b` is the strongest local option and needs about 8 GB of GPU memory. Use `qwen2.5vl:3b` on weaker PCs.
  - `gemma3:4b` and `gemma3:12b`.
  - `llama3.2-vision`.

The Workspace shows how often the model agreed with the observer (see §5), so you can compare models on real data.

## 2. Running a session

```powershell
.venv\Scripts\python -m app.main             # participant window fullscreen on screen.monitor_index
.venv\Scripts\python -m app.main --windowed  # participant window in a normal window (testing)
```

1. **Workspace → Live → pre-flight:** choose the following, then press **Start session** (enabled once gaze samples arrive):
   - the condition and gaze source
   - the LLM
   - the **camera**: `usb` with a device, `mock` or `off`
   - the **step checker**: `gemini`, `ollama`, `mock` or `off`
   - gamification on or off at the start
   - whether the eye-tracker check runs first
2. **Intake:** the participant enters:
   - a 7-character ID (A–Z and 0–9)
   - age, typed as a number
   - the questionnaire answers
   - the item to build, if more than one set exists

   An ID that already has a session folder is rejected.
3. **Eye-tracker check:** a ring collapses on each target in turn. In Live, the experimenter chooses **Accept & start**, **Repeat check** or **Skip check**.
4. **Player:** "Next" is a mouse click or the `input.next_key` key (a USB buzzer mapped to Space works). After every "Next", the board is photographed and checked **in the background**, so the participant never waits (see §5).
5. **"Repetition N completed!" → assessment:**
   - Part 1: difficulty, and compact Yes / No / More detailed.
   - Part 2: remove (or show again) videos, text, images or gamification.
6. If something should change, the LLM processes the request. The **trial intro** explains the change, then the participant does the **trial run** and chooses **Keep** or **Back to previous version**.
7. **End session** shows the final questionnaire (SUS), then "Session complete". **Ctrl+Shift+Q** in the participant window ends the session immediately, without the questionnaire.

Every run gets a `run` number. Only regular runs get a `repetition` number, so a trial never shifts "Repetition N".

## 3. The Workspace

| Section | Contents |
|---|---|
| **Live** | Pre-flight settings and hardware telemetry: tracker, sample rate, camera. These collapse while a session runs. **Stimulus mirror**: AOIs, current step and the live point of regard. **Camera check**: reference image and board photo side by side, the verdict with confidence, Correct / Error to confirm or correct it, and a **Needs review** queue. **Observer verification** (Ctrl+1 / Ctrl+2), the participant's latest answers, and the calibration gate. The header shows the tracker, state, participant, run, the number of checks to review, Drift check, Export and End session. |
| **Recordings** | Every stored session, newest first, with a thumbnail of its last board photo, the time spent assembling, the date, condition, runs and status. You can filter the list, see the details of a selected session, and use **Open in Visualizations**, Show in folder and Export. |
| **Enrichments** | **Instruction sets** (steps, text, MTM time, media, reference image); **Media library** (files plus a one-sentence description for the LLM); **Reference images** (one photo per step of the correct board after that step, imported or taken with the camera using the live preview); **LLM rules**. Everything is validated and saved as JSON. |
| **Visualizations** | Shows the live session or any recording. **Runs**: step time vs. MTM, AOI dwell and the metrics table. **Analytics**: time across runs, **errors per run**, steps with errors, intake answers and SUS score. **Camera checks**: every check with photo vs. reference, and how often the model agreed with the observer. **LLM calls**: the full prompt and raw output. **Decisions & versions**. |
| **Downloads** | Every file the app produces, by session (logs, summaries, photos, versions, exports) plus app files (config, instruction sets, media library, rules, prompts, logs, AOI layouts). You can open a file, show it in its folder, save a copy, export a session package, or **export the AOI layout** as JSON and PNG. |

## 4. Gamification (can be removed by the participant)

All elements sit inside the existing AOIs, so the gaze data stays comparable whether gamification is on or off.

| Element | Where | Behaviour |
|---|---|---|
| **Guide** | text AOI | A builder with a Duplo-yellow hard hat who *says* each step in a speech bubble. The mouth moves for `speak_s` after a new step, then smiles. The eyes look at the participant and blink. |
| **Step track** | progress AOI | One segment per step (done / current / to come). It is green during a trial run. |
| **Streak** | progress row | Counts steps verified correct in a row, by the observer or the camera (or repetitions without "Back", depending on `streak_mode`). It flashes when it grows. |
| **Points** | progress row | `points_per_step` once per step, plus `points_correct_bonus` when a step is verified correct. Going Back and pressing Next again doesn't earn the points twice. |
| **Cheers** | small tag on the speech bubble | Only at the `cheer_streaks` milestones (3, 5, 10), shown for `cheer_s` seconds |
| **Level** | progress row | Level 0 Base, 1 Compact, 2 Pro (follows the merge level). Unlocking a level is shown on the trial intro. |
| **Run results** | "Repetition completed" screen | New personal best, steps verified correct, points earned in this run |
| **Trial stars** | trial feedback | Stars from the time vs. the MTM reference |

All values are in `config.yaml` → `gamification`.

## 5. Camera check (automatic error verification)

1. After every "Next", the board is photographed (`camera.settle_ms` later, so the hand is out of the way). The photo is saved as `photos/run001_step03_0003.jpg`.
2. A background worker sends the step's **reference image** and the photo to the step checker, together with the step text, part, quantity and positions. The prompt is in `data/prompts/verification.txt`.
3. The answer must be strict JSON: `verdict` (correct / error / unclear), `confidence` (0–1, shown as %) and a short `reason`.
4. When the confidence is at least `verification.threshold` (default **80 %**), the verdict is applied automatically. It updates the step metrics, the streak and the points, and it updates the run summary even if the run has already ended.
5. When the confidence is lower, the photo is unclear, there is no reference image, the checker is off or the call failed, the step goes to **Needs review**. The observer decides with Correct / Error in Live or in Visualizations → Camera checks.
6. **Priority:** an observer review beats the live observer button, which beats an automatic camera verdict. Each step ends up with exactly one final verdict.
7. Every check is logged. A review is written again with `reviewed` set, so you can report the model's accuracy against the observer.

Reference images are made in **Enrichments → Reference images**: take a photo per step with the study camera in its study position, or import one. A combined step uses the reference of its last base step. `python -m tools.make_placeholders` creates drawn placeholder references for the sample set.

## 6. Testing without hardware

- **Click-through:** gaze `mouse`, LLM `mock`, camera `mock`, checker `mock`, and `--windowed`. The mock camera "photographs" the reference image; `camera.mock_error_every_n` simulates assembly errors.
- **Fake tracker:** gaze `mock` starts a fake GP3 TCP server on port 4343. To run it on its own: `.venv\Scripts\python -m app.gaze.mock_server --port 4343 --rate 60`.
- **Automated tests** (headless, no hardware, no network): `.venv\Scripts\python -m pytest -q`. Last run: **162 passed**. They cover:
  - the trial-numbering rule
  - LLM plan and generation, and time-only stripping
  - the full loop and intake validation
  - the camera check: every step photographed and checked, review queue, priorities, missing references, camera failures, session end waiting for running checks, archive and export
  - the vision clients and strict validation, using a fake Ollama server
  - the Workspace pages
  - gamification rules (points once per step, cheers, personal best)
  - break tests (path traversal, hostile file names, corrupt files, HTML injection)
  - that every text key used in the code exists

## 7. Session output

Each session writes `sessions/<participant_id>_<YYYYmmdd_HHMMSS>/`:

| File | Content |
|---|---|
| `session.json`, `config.yaml` | participant, intake answers, settings; the config used |
| `gaze.jsonl` | every gaze sample |
| `events.jsonl` | step events and every state transition |
| `summaries.json` | one RunSummary per run (per-step metrics incl. verified errors) |
| `observations.jsonl` | every applied step verdict, with `source`: observer / camera / review |
| `verifications.jsonl` | every camera check: photo, reference, model, verdict, confidence, reason, review |
| `photos/` | board photos (one per step and run) |
| `llm_calls.jsonl` | every LLM call: full prompt, raw response, validation, latency |
| `decisions.jsonl`, `calibration.jsonl`, `survey.json`, `versions/` | decisions, eye-tracker checks, questionnaire, instruction versions |

Writes are atomic, and the loader tolerates damaged records and older session formats. **Export** writes CSVs (incl. `verifications.csv`) and `session_data.json`.

## 8. Customising (nothing is hard-coded)

| What | Where |
|---|---|
| All settings: AOIs, keys, ID pattern, intake, calibration, loop, gamification, camera, verification, LLMs, limits, logging | `config.yaml` (or the file in env `GAZE_APP_CONFIG`) |
| Every user-visible text | `data/locale/en.json` |
| Colours, fonts, chart palette, icons | `data/theme.json` |
| Instruction sets, media library, reference images, LLM rules | Workspace → **Enrichments** (stored as JSON under `data/`) |
| LLM prompt wording (layout and step check) | `data/prompts/*.txt` |
| Final questionnaire | `data/surveys/sus.json` |

## 9. Security and robustness

- **Paths:** every content and media path is relative and must resolve inside its folder. Imported files get safe names, an allowed type and a size limit.
- **Text:** text from files, the LLM or participants is never shown as HTML.
- **Limits:** reads are size-limited, and LLM responses and photos sent to the checker are capped and downscaled.
- **Local only:** the mock tracker and Ollama connect to local hosts only.
- **API keys:** read only from environment variables.
- **Background work:** LLM calls and camera checks run in background threads. At the end of a session, checks still running are waited for (up to `verification.drain_timeout_s`) before the log closes.
- **Errors:** uncaught errors go to `logs/app.log` and a dialog instead of closing the app.

## 10. Packaging

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

The script runs the tests first. It then builds `dist\GazeAdaptiveStudy\GazeAdaptiveStudy.exe` and copies `config.yaml` and `data\` next to it, so they stay editable.

## 11. Before the study: things to verify

- **TODO-VERIFY (live GP3):** the Open Gaze API names in `config.yaml` were checked against the *Open Gaze API Ver. 2.0* PDF, but not yet against a real GP3.
- **Camera position:** mount the USB camera so it sees the whole board from above. Take the reference images from exactly that position, and check the photos in Live during a pilot run.
- **Step checker:** run a pilot with the chosen vision model. Review its checks and look at the agreement shown in Visualizations → Camera checks. Adjust `verification.threshold` if needed.
- **Eye-tracker check geometry:** set `calibration.screen_width_cm`, `viewing_distance_cm` and `max_error_deg`.
- **Rules and prompts:** `analysis_rules.json` is marked **DRAFT**.
- **Monitor:** set `screen.monitor_index` to the tracked monitor.

## Project structure

```
app/
  main.py, config.py, i18n.py, clock.py
  core/        files (safe paths, atomic writes, caches), repository (content JSON, media import)
  models/      pydantic models: instruction, gaze, metrics, LLM I/O, logs, content, verification
  camera/      board camera: USB webcam (Qt Multimedia), mock, off
  vision/      step checker: Gemini / Ollama / mock clients, check job + validation, background queue
  ui/          participant: intake, calibration, player (speaking guide), saved, assessment, analysing,
               trial intro, trial feedback, survey; Workspace: workspace, live_page, recordings_page,
               enrichments, visualizations_page (session_tabs, charts), downloads_page, mirror,
               setup_screen, workspace_widgets; style, icons (QtAwesome), theme
  gaze/        source interface, GP3 client, mouse source, mock server
  pipeline/    AOI mapping, logger, preprocessing, summaries, verdicts, calibration, archive, export,
               AOI layout export
  llm/         client interface, factory, Gemini, Ollama, mock, prompts, validation
  loop/        controller (session state machine)
data/          instruction_sets/, media/ (+ catalog.json, references/), rules/, prompts/, locale/, surveys/
packaging/     PyInstaller spec + build script
tests/         pytest suite
tools/         placeholder media + reference image generator
```
