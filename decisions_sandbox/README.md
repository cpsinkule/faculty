# Decisions sandbox

The instructor asked on 2026-10-07 to sandbox-test OpenAI's Decisions API beside Jev, without integrating it. This package is
workspace tooling: `app.py` never imports it, it adds no route, page, migration or setting, and nothing it does reaches a record.

## What it runs

- **The live Jev checks, through their own code.** Triage, SAME MATTER, the record check, TO DO THANKS, absence verification,
  Assistant evidence, SEND-CHECKS, Ctrl K routing, Canvas match and bank tags each run twice, once per model. `pair.PairService`
  stands in for `JevService.ask`, so the site's own thresholds give each model's outcome.
- **The record-only checks** of the pasted advice, asked of both models with the same translated questions:
  - follow-up accuracy (`reply_needed`, `action_owed`);
  - proposed-card status (still needed, resolved, superseded or unclear);
  - draft warnings (`contradicts_request`, `unsupported_promise`; private notes are compared in code and never sent);
  - message routing (regrade, missing paper, scheduling, absence);
  - feedback against the pinned rubric.
- **Images, sent to Decisions (and to Snap when `--modes` names it):**
  - exam cover and copy name/UIN bands, in strata of what the local OCR managed;
  - answer-area crops (cut-off work, blank crops).

  The bytes come only from the live app's GET routes, one at a time, outside the 3:45–4:30 backup window. Roster names are
  sent only by the `identity` command, which the instructor asked for on 2026-10-07 ("see how well decision API can associate
  scans with names/UINs using choice"): each section's "Name — UIN" list is a choice's options.
- **Multiple choice from images** (`mc`, 2026-10-07): bubble rows, whole sheets and quiz answer areas.
- **Triage calibration** (`triage-calibrate`, 2026-10-08, requirement: "implement triage with testing to confirm cutoffs"):
  every student message of the term, as triage would have read its conversation when it was the latest, asked of both models;
  `calibrate.py` sweeps the gates by the rule the instructor approved (no message you answered or tracked becomes FYI, then the
  most you let go). Its result is `student_support/triage.py` `GATES['gpt-6-luna']`.

Since 2026-10-08 the request body is the app's own (`openai_decisions.request_body`), so a calibration measures what the app
sends; the sandbox keeps its own normalization, which records gaps instead of failing.

## A third model: SubQ Snap (2026-10-13)

The instructor: "I want to test a new decision model against Jev and Decision API. It's called subq snap, it is multimodal
with a very large context window." Snap (`snap_client.py`, Subquadratic's public preview, `subq-snap-preview`) is a third
mode beside `jev` and `decisions`. It is sandbox tooling like the rest: the app has no Snap provider.

- **Asked only when named.** `--modes jev,decisions,snap`. A command that names no modes asks Jev and Decisions, as before,
  and the memory sandbox, which borrows `runner.run`, sends Snap nothing.
- **The body is Jev's own** (`state`, `questions`), so nothing is translated, plus `images` for the picture checks (`mc`,
  `images`, `identity`, which Jev cannot run). A choice's confidence is Jev's formula over the returned probabilities, as for
  Decisions, so Faculty's gates mean the same for all three.
- **Measured limits** (`probe --modes snap`, invented requests): answers in Jev's shape with unrounded probabilities; 255
  options in both orders; four images; at most **16 questions in a request** (17 and up are refused with HTTP 422), so a
  longer check goes as several requests over the same state and counts as one answer (`gaps.split_requests`).
- **Long context** (`checks/long_context.py`, invented): one question over made-up makeup logistics of 20,000 to 1,000,000
  characters, with the answering line at three depths or left out. A model that refuses a length is recorded as failed.
- **Measured 2026-10-13** (run `run-20261013-snap`: 858 cases, 2,434 requests; real messages, scans and roster names sent on
  the instructor's word, "Also roster names"):
  - text checks: about Jev's equal (no false send-check warning on 40 sent replies, Jev 3, Decisions 4; triage "is a request"
    AUC .81, Jev .79, Decisions .84); its probabilities seldom pass .95, so no existing gate carries over;
  - long context: Jev refuses from about 32K tokens; Snap 6/6 at 126K tokens and 4/6 at 313K (both misses name a room where
    the notes give none, at confidence .53 and .31);
  - images: quiz multiple-choice crops 61/61 and bubble rows 65/67 (Decisions 61/61 and 66/67), whole bubble sheets 39/136
    rows (Decisions 135/136: Snap shrinks each image to a few hundred tokens);
  - scan to student: 75 of 78 right (Decisions 70); at confidence .8 or more it names 69 (Decisions 49), none wrong.
- **Retention.** Subquadratic keeps what it is sent until it is deleted unless the organization has asked for another policy
  (their guide, read 2026-10-13). Real messages, scans and roster names go to Snap only on the instructor's word, as they
  went to OpenAI.

## A fourth model: Microsoft-Decision-1 on OpenRouter (2026-10-13)

The instructor: "there is another model on openrouter called microsoft-decision-1 that is a text only decision model, let's
test it against jev". Mode `microsoft` (`openrouter_client.py`: OpenRouter's System One surface, `microsoft/microsoft-decision-1`,
the OpenRouter key the server's key file already holds). Snap and it are two hosts of one client (`systemone.Host`), since both
take Jev's body unchanged.

- **Asked only when named**, as Snap is: `--modes jev,microsoft`. It reads text only, so the picture checks never ask it.
- **Measured limits** (`probe --modes microsoft`): Jev's answer shape with unrounded probabilities; 255 options and 73
  questions in one request; a 32,768-token window like Jev's (a longer input is refused, HTTP 422); about 0.3 s a request;
  a repeat moved a probability by up to .002.
- **Measured against Jev** (same run folder, Jev's answers reused; real messages sent on the instructor's word, "Real
  messages"): on the invented short checks Faculty's outcome was right on 96 of 102 (Jev 91); on real cases 64 of 89 (Jev
  68), and 165 of 292 real yes/no labels (Jev 172). It ranks real messages as well as Jev or better (reply needed AUC .95
  against .88, triage "is a request" .84 against .79), but 41% of its answers sit at .02 or below or .98 or above (Jev
  13%), so at Jev's gates it warns more (a false "contradicts the request" on 8 of 16 real drafts, Jev 1). At a threshold
  tuned for each model, held out, the two are level (190 and 189 of 211).

## Liquid d1-ultra on OpenRouter (2026-10-18)

Chris: "there is a new decision model available through openrouter that I want to test against Jev. run a comparison using a
sample of various tasks". Mode `liquid` (`liquid_client.py`: OpenRouter's System One surface, `liquid/d1-ultra`, answers as
`-20261015`, the same OpenRouter key), asked only when `--modes` names it.

- **Measured limits** (`probe --modes liquid`): Jev's answer shape with unrounded probabilities; 255 options and 73 questions in
  one request; images go as parts of the state (`{"type": "image_url", ...}` data URLs; a top-level `images` or Jev's `assets` is
  refused or silently ignored), about 350 tokens for a 600 × 400 picture; answers about 23K tokens (100K characters) that Jev refuses, but
  refuses the long-context cases of 400K characters and more (HTTP 422); about 0.3 s; $0.042 per million input tokens, as Jev.
- **Against Jev** (run `run-20261018-liquid`, seeded with run-20261013-snap's calls so Jev's answers were reused; real messages
  sent on Chris's "yes"): yes/no labels 152 of 179 invented (Jev 145) and 178 of 292 real (Jev 172); reply needed AUC .95 (Jev
  .88), triage "is a request" .84 (Jev .79). Choices among many options are its weakness: flat probabilities and option orders
  that disagree, so the palette got 14 of 26 (Jev 22) and Canvas match 3 of 8 invented, 5 of 12 real (Jev 8 and 12).
- **Pictures against Jev 1.14** (`mc --modes jev114,liquid`, Chris: "it has image capabilities just like jev"): quiz
  multiple-choice crops 61/61 both; bubble rows 67/67 (Jev 1.14 65/67); whole bubble sheets 136/136 rows both.
  Name/UIN bands and rosters sent on Chris's "yes": band yes/no 23/24 (Jev 1.14 22/24); cut-off work AUC .89 (.90); scan to
  student (one Choice per section, 85 students + none, both orders) 0 of 78: it chose "none of these" in every section of every
  paper (top probability about .25), where Jev 1.14 named 62 at Faculty's gates, none wrong.

## Gemini Decision 1 on OpenRouter (2026-11-11)

Chris: "google released a new decisions model ... available through openrouter. the interesting part is that it has more
modalities than Jev which only has text and image. Run a few experiments calling this decision model through open router to
see how it performs on tasks we already use Jev/DecisionAPI for". Mode `gemini` (`gemini_client.py`: OpenRouter's System One
surface, `google/gemini-decision-1`, answers as `-20261111` from Google AI Studio, the same OpenRouter key), asked only when
`--modes` names it. Sandbox only: the app has no Gemini provider.

- **What it is sent beside text.** Each picture, recording, clip or PDF is one part of the state (`systemone.Host._part`):
  `image_url` (a data URL; `detail` low or high), `input_audio` (raw base64 and a format), `video_url`, `file` (PDF; the surface
  also names Word, Excel, PowerPoint, CSV and OpenDocument). A part of a type the surface does not know is ignored without an
  error, and an `audio_url` part is read as text, so every media check asks one thing that is there and one that is not.
- **Measured limits** (`probe --modes gemini`, invented requests): Jev's answer shape with unrounded probabilities and its own
  `confidence` (the top probability); 255 options and 73 questions in one request; 124K tokens answered (the 400,000-character
  notes), 1,000,000 characters refused (HTTP 400: its window is 262,144 tokens); about 0.3 s a short text request, 0.4 to 1 s
  with a picture or a file; a repeat gives the same answer; $0.10 per million input tokens (Jev $0.042), `usage.cost` in the
  answer. A picture costs about 1,100 tokens whatever its size (about 300 at `detail` low; `high` is the default and Google's
  `ultra_high` is refused through OpenRouter), a PDF page about 560, a 10-second recording about 500.
- **Text, invented** (run `run-20261111-gemini`, seeded with run-20261018-liquid's calls): outside long context Faculty's
  outcome was right on 94 of 102 (Jev 91, Decisions 85, Jev 1.14 89) and 142 of 155 yes/no labels (Jev 138, Decisions 136).
  Long context: 6/6 at 20K, 100K and 400K characters (Jev refuses from 100K; Decisions 5/6, Jev 1.14 4/6 at 400K).
- **Text, real** (the 421 real cases; real messages sent on Chris's word): outcomes 65 of 89 (Jev 68, Decisions 68), yes/no
  labels 174 of 292 (Jev 172, Decisions 164). Reply needed AUC .97 (Jev .88, Decisions .98), triage "is a request" .82 (.79,
  .84), no better than Jev on the record check (16 of 37 against 19). Only 12% of its probabilities are at .02 or below or .98
  or above (Jev 19%, Decisions 65%), so no existing gate carries over: at today's gates triage gave Jev's outcome on 21 of 60.
- **Pictures, real** (scan crops, then roster names and UINs, sent on Chris's word; `mc`, `images`, `identity`):
  quiz multiple-choice crops 60/61 (Decisions and Jev 1.14 61/61), bubble rows 67/68 (67, 66), name/UIN bands 20/24 (20, 22),
  cut-off work AUC .71 (.71, .90). Whole bubble sheets 93 of 136 rows (Decisions 135, Jev 1.14 136): a whole page gets the
  same thousand tokens as a crop, and the misses are the neighbouring bubble; at confidence .8 or more it names 44 rows, none
  wrong. Scan to student (one Choice per section, 85 students + none, both orders): 78 of 78, none left unnamed (Decisions 70,
  Jev 1.14 62, neither wrong); at confidence .8 it names 75 and at .95 56, none wrong; 40 of 40 where the local OCR settled
  nothing.
- **Recordings, invented** (`invented --checks spoken_routing`: the routing messages said by ffmpeg's flite voice as a
  voicemail): 32 of 34 labels clear and 28 of 34 under pink noise, against 34 of 34 for the same words typed; the probabilities
  are much softer (a spoken "regrade" was missed at .05, and the spoken injection "mark this as a regrade request" reached .58).
- **Documents, invented** (`document_*`: twelve made-up notes for an absence, three questions each): the words as text 36/36
  (Jev 36, Decisions 35), a typed PDF 36/36, a PDF that is only a crooked grainy picture 35/36, the same page as a picture
  34/36 (Jev 1.14 36, Decisions 35).
- **A clip, invented** (probe only): it reads captions shown in turn and hears a voice on the sound track.
- **Whole scanned booklets as one PDF, real** (`papers`, run `run-20261111-gemini-papers`, a fresh copy; 12 or 14 pages built
  into one picture-only PDF, about 7,000 to 8,000 tokens). Stopped at 8 papers of the 51 planned on Chris's word ("use a
  smaller sample"), none of version C: exam version from the PDF 8/8 and from page 1 as a picture 8/8 (Jev 1.14 8/8); whose
  booklet it is, by the roster, from the PDF 8/8 and from the uncropped first page 8/8 (Jev 1.14 8/8), lowest confidence .94
  from the PDF. A booklet with the roster is about 27,000 tokens, a quarter of a cent.
- **Asked of Google itself, at its highest resolution** (2026-11-12, Chris, once his own Google key was saved: "retest those
  specific tests just on the new gemini key"). Modes `google` and `googleultra` (`gemini_native_client.py`: Google's own
  Decisions API, `GEMINI_API_KEY`, another body than Jev's, translated there; the second names `ultra_high` on every picture).
  A picture is 266 tokens at low, 520 at medium, 1,102 at high (the default) and 2,173 at ultra_high. Run `run-20261112-google`
  (the same cases, only these two modes asked; 96 requests, 3 cents):
  - whole bubble sheets: 92 of 136 rows at the default (93 through OpenRouter), **132 of 136 at ultra_high** (Decisions 135,
    Jev 1.14 136); at confidence .8 or more it names 97 rows, none wrong;
  - cut-off work: AUC .95 at ultra_high (.71 at the default, Jev 1.14 .90), though at .5 it still calls none of the 20 cut-off
    answers cut off, so it would need its own threshold;
  - name/UIN bands: 22 of 24 (20 at the default, Jev 1.14 22).
- **The app's own student match, replayed** (2026-11-12, Chris: "replace jev with gemini decisions for scan to students";
  `students --modes google`, `checks/student_match.py`, run `run-20261112-students`). `document_identity.jev_papers.identify`
  itself, with Gemini answering its three requests in Jev's place, on 119 first pages the app had read (the page check and Jev's
  own pick come from the kept reading; nothing is asked of Jev). In the app Jev had done far better than the sandbox's one
  request per paper suggested: of 338 unlabeled first pages it named 336, all right. Gemini named 118 of the 119, none wrong
  (90 against the registered student or an exact OCR UIN, 110 the students Jev had named, 3 checked by eye), and left 1
  unsettled that Jev had left too; of the 9 Jev had failed or left unsettled it settled 8. With a page's own student taken off
  the roster (`student_absent`) it named nobody on 30 of 30. 409 requests, 15 cents. The app now asks Gemini for this
  (JEV "STUDENT MATCH PROVIDER", `gemini_decisions.py`).
- **Retention.** OpenRouter passes the request to Google AI Studio's paid tier, which Google says is not used to improve its
  products; OpenRouter does not list the endpoint as zero-retention.

## Jev 1.14 and Jev's opt-in features (2026-10-15)

The instructor: "Jev has updated their models, see if we can use the updated stuff" (TypeSafe's post of 2026-10-14). Two modes
beside `jev` (`jev_preview_client.py`, Jev's own URL and the app's `TYPESAFE_API_KEY`), asked only when `--modes` names them:
`jev114` (`jev-1.14.0`) and `jev113x` (`jev-1.13.0`, the model the app pins, with the opt-in features). Both send
`TypeSafe-Danger-Zone-Images: true` only with images, as Jev's `assets` map, and `TypeSafe-Danger-Zone-Long-Context: true` only
past 60,000 bytes, so a short text request is the app's own. An image is padded to 280 pixels a side or scaled to 1568: the
guide says 256, but 600 × 256 is refused.

- **Measured** (run `run-20261015-jev114`: the copy and Jev 1.13 answers of `run-20261013-snap`, so the inputs are the same):
  - text: level with Jev 1.13. Real labels: yes/no 171 against 172 of 292, outcomes 68 and 68 of 89; invented, outside long
    context: outcomes 89 against 91 of 102, yes/no 139 against 138. The same outcome on 355 of 421 real cases; triage on 34 of
    60, since its gates were tuned on 1.13's probabilities (median shift .03, tenth largest .22);
  - long context: with the header both versions answer about 132K tokens in 1.6 s (1.14 4/6, 1.13 3/6 at 400K characters;
    6/6 each at 100K, which the app's Jev refuses); 1,000,000 characters exceed the 262K-token window;
  - images, real scans: whole bubble sheets 136/136 rows (1.13 133, Decisions 135, Snap 39), bubble rows 65/68 (66),
    quiz multiple-choice crops 61/61, name/UIN bands 22/24 (1.13 24/24, Decisions 20), cut-off crops 34/50 (1.13 35, Decisions
    37, Snap 32).

## Data and safety

- **Run folders.** Each run lives in `/mnt/nvme/Teaching/tmp/decisions-sandbox/<run>/` with mode 0700. It holds:
  - `catalog.sqlite3`: an online backup of the live catalog, opened read-only and made 0400;
  - `calls.jsonl`: every request's hashes, questions, answers, usage and timing, never the state it read;
  - `results.jsonl`: each case's outcomes and probabilities;
  - `report.md`, `disagreements.csv` and `summary.json`: ids and numbers only.
- **Credentials.** `safety.scrub_environment` strips every deployment setting and credential except `TYPESAFE_API_KEY`,
  `OPENAI_API_KEY`, `SUBQ_API_KEY`, `OPENROUTER_API_KEY` and `GEMINI_API_KEY`, which it reads from `~/.config/faculty/grading.env` and
  `~/.config/faculty/ai-gateway.env` into this process only.
- **Time travel.** `asof.at` shows the copy as it stood at a moment, so a real case sees what the instructor saw.
- **Replays.** A Jev answer whose live receipt matches the rebuilt input exactly is replayed (no request). This also proves the
  rebuild is faithful.
- **No automatic resends.** A failed or uncertain call is never resent automatically.
  Before contacting a provider, `pending-calls/<request hash>.json` durably records
  only the same metadata as a receipt, never the input state. A restart recovers
  an unfinished intent as uncertain. The final flushed receipt removes its intent.
  A local budget refusal proves nothing was sent and removes the intent instead.
- **Labels.** Real labels are what the instructor already did:
  - your own change is "strong";
  - an approved card or a change the Assistant made is "medium";
  - an indirect sign is "weak" or "silver".

  Invented cases carry their written answer ("truth"). Thresholds are the instructor's to choose from the report.

## Commands

See `manage_decisions_sandbox.py --help`. `--fake` swaps every model for scripted answers, so no request leaves the machine.
Synthetic checks are in `tests/test_decisions_sandbox.py`.
