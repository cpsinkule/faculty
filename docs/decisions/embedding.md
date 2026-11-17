## Embedding

Rule ID: **EMBEDDING**. Current binding module contract. Read root `AGENTS.md` first, then ASSISTANT-MEMORY.

- The instructor (2026-10-10), asked which embedder the Assistant's memory should use after the sandbox: **"I want full
  embedding model, we can fix the gpu for this"**. EmbeddingGemma 2 at its full 768 dimensions, on the GPU. On the
  sandbox's invented cases it put 77 of 110 needed memories in the nearest eight where MiniLM put 70 and keywords 59, and
  the right follow-up lesson in the first four for 27 of 30 requests where keywords found 12.
  `faculty_embedding/` (config, setup, launch, worker, client, `model-lock.json`, `requirements.lock`),
  `deploy/faculty-embedding.service`, `deploy/faculty.service.d/embedding.conf`,
  `deploy/faculty-inbox.service.d/embedding.conf`, `tests/test_faculty_embedding.py`.
- **OWNER.** Workspace computing: a model, a venv and a socket under `/mnt/nvme/Teaching/runtime/embedding` (STORAGE: never
  the root disk). It owns no teaching record and no table. The worker keeps nothing it computes: text vectors live in its
  memory, at most 20,000, and are gone when it stops; a picture and its vector are never cached or written by it. What
  Faculty keeps of an answer is the caller's to own and say (PICTURE-SEARCH keeps the vectors of pictures, and of the words
  Faculty stores about them, in its own rebuildable index; PAST-WORK keeps two vectors for each of the Assistant's
  recorded actions in another; RECORDINGS keeps one vector for each passage of a kept transcript in a third).
- **PINNED AND OFFLINE.** `google/embeddinggemma-2` at one revision, every file held to `model-lock.json` (size and
  SHA-256; safetensors and configuration only, no code and no pickle). `python -m faculty_embedding.setup` is the only step
  that downloads; the worker verifies the lock before it loads and refuses a model that differs. Remote model code is never
  enabled, and a hosted embedding API is never substituted: when the worker cannot answer, keywords stand in.
- **THE WORKER** (`worker.py`, started by `launch.py` under bubblewrap) is one long-lived process with no network, no home
  folder, no Faculty checkout beyond this package, no catalog and no credential; its only writable folder holds its socket
  (mode 0600). It loads the text and picture parts of the model (never the audio part), on the GPU when that works
  (`FACULTY_EMBEDDING_DEVICE`: `auto`, `cuda`, `cpu`), and may take at most a quarter of the GPU's memory. Local OCR keeps
  its share beside it (measured 2026-10-11 on the card's 10,820 MiB: its largest read, first pages in batches of eight,
  peaked at 9,610 MiB with the worker loaded; every other read under 6,000); the local grading model does not fit beside it and borrows
  the card (LENDS THE GPU). It writes no text or picture anywhere.
- **FLOAT32 ONLY** (requirement, 2026-10-10: "make sure the embedding model is running in fp32, google warns about trying
  to use fp16"). The weights are converted to float32 on load whatever precision the file stores them in; nothing uses
  half precision, bfloat16, autocast or quantization; a model with any other parameter type does not start; and `status`
  reports `precision`.
- **PICTURES AT 560 PIECES** (requirement, 2026-10-11, shown 280, 560 and 1120 measured on real pages: "switch to 560").
  The model cuts a picture into pieces ("soft tokens") and sees no finer than that; the count is the model's own setting
  (70, 140, 280, 560 or 1120; it ships at 280), asked for with each picture (`PICTURE_PIECES`, `encode_pictures`). At 560 a
  Letter page is seen at about 1,000 by 1,290 pixels where 280 saw 670 by 960, so the worker now sizes a picture to 1,568
  pixels on its longest side (`PICTURE_SIDE`; 1,024 would undo the setting). Text is untouched: 768 numbers come out either
  way. Measured that day on a worker of its own, in float32 and the same quarter of the card (70 blank printed pages and
  about 170 scans of Exam 1 and Quizzes 2 to 4; then 200 graded answers of five questions, searched by their stored
  transcriptions on the instructor's "allow it"; the 280 vectors matched the live index's at .997):

  | | 280 | 560 | 1120 |
  |---|---|---|---|
  | seconds a picture | 0.21 | 0.44 | 1.24 |
  | the card at its peak, MiB | 2,076 | 2,274 | 3,044 (given half the card) |
  | fits in the worker's quarter | yes | yes | no: out of memory on the first picture |
  | one question's text finds its printed page first (of 70) | 44 | 55 | 53 |
  | ten topic words find a right printed page first | 9 | 9 | 9 |
  | a Quiz 4 scan finds its own version's blank page by picture (of 80, among 18) | 26 | 66 | 78 |
  | an answer's transcription finds its own scanned page first (of 200, among 40) | 21 | 47 | not run |
  | the same for the answer's crop | 47 | 59 | 52 |

  So 560 costs twice the time, nothing of the card's shares, and tells look-alike pages apart far better by picture; 1120
  adds little, is three times slower again and does not fit. A vector made at another setting cannot be compared with these
  (the same page at 280 and at 560: .96), so picture vectors are kept under a mark that names the setting (`PICTURES`), the
  worker says how finely it saw (`pieces` with the vectors, `picture_pieces` in `status`), and Faculty takes no vector from a
  worker on another setting (`OtherSetting`: a wait that blames no picture, PICTURE-SEARCH). Changing the setting embeds
  every kept picture again.
- **FACULTY ASKS ONLY WHEN TOLD.** Faculty reaches the worker only when `FACULTY_EMBEDDING_SOCKET` names its socket (the
  web app's and the Inbox worker's drop-ins). A check, a script or a second copy of the app never reaches the live worker.
  A text answer is waited for 1.5 seconds and a picture's for 20; no worker, a slow answer or a refusal is
  `EmbeddingUnavailable`, and the caller falls back or waits. A refusal that is about the picture itself (not a picture, not
  PNG or JPEG, too large) is marked (`"picture": true`, `PictureRefused`), so a caller that keeps an index sets that one
  picture aside and never mistakes a worker that is down for a bad page.
- **WHAT IS SENT.** Text Faculty already holds as the instructor's own (a request they typed, kept memories, a description
  typed into a search, a picture they hand over to search by or to be remembered, and the words Faculty stores about course materials: a question's text, a picture's caption, a
  section's name, a notes page's own text) and pictures Faculty already holds (PICTURE-SEARCH names which, and which words).
  No words about a student's file are sent. A picture is decoded inside the worker's jail and nowhere else. Nothing leaves
  the machine. One more kind of text since 2026-10-10 (PAST-WORK, the instructor's "build it"): the Assistant's recorded
  actions, each as its summary and the words of its own request. These name students and quote mail, which no other use
  sends; they go to this worker and nowhere else, and only their vectors are kept. And since 2026-10-11 (ASSISTANT-MEMORY "A
  FOLLOW-UP ASKS WITH THE STUDENT'S LAST MESSAGE", the instructor: "use the student's last message"): the latest message a
  student wrote in a follow-up, cut to 1,500 characters with the roster's names taken out, as the request its preparation
  finds kept memories with. It is embedded as a request, and a request's vector is never kept. And since 2026-11-14
  (RECORDINGS "Search by meaning", step 6 of the instructor's recordings plan: "transcript text in the embedding index";
  that it covers every kept recording is my reading and theirs to narrow): THE WORDS OF KEPT TRANSCRIPTS, in passages of
  about 1,000 characters, words alone (no speaker name is added, no time), of the latest transcript of every recording
  filed as Faculty's now, a student's meeting and a class included. They hold names and what students said, as past
  work's texts do: they go to this worker and nowhere else, and only their vectors are kept, in Recordings' own
  rebuildable index, which holds no word. A recording that waits for Keep or Don't keep, or is not kept, has no kept
  transcript and nothing of it is sent; one the instructor said not to keep after it was kept is sent no more and its
  vectors are dropped (RECORDINGS "ONLY WHAT IS FACULTY'S IS KEPT"). The
  description typed into the Recordings search is embedded as a request, and its vector is not kept.
- **ONE AT A TIME.** Text and pictures share one model and one lock. A caller that embeds many pictures sends one a request,
  so a memory lookup waits behind at most one picture. A caller that embeds long texts sends one a request too, of at most
  1,500 characters: the worker's quarter of the GPU does not hold a batch of long texts (2026-10-10: sixteen question texts
  in one request were refused for memory; the worker kept serving).
- **LENDS THE GPU** (requirement, 2026-10-11, shown that the model which reads a blank exam's answer areas ran out of GPU
  memory beside the worker: "build the hand-off"). Faculty's local grading model (GRADING's fully local endpoints: the answer
  areas of an uploaded blank exam, which Faculty starts by itself; a fully local batch; an imported rubric's interpretation)
  needs the whole card. Faculty therefore borrows it for exactly one request to that model (`client.gpu_lent`, around the
  request in `LocalVlmProvider`; a cloud host borrows nothing, and OCR reads beside the worker). `lend`: the worker finishes
  the request in hand, moves the model to main memory and frees the card but for its own CUDA context. `take_back`, sent
  when the request is over however it ended: the model goes back on the card, which is watched up to 15 seconds for room
  while the other model unloads. While lent the worker refuses every request but `status` (which says `"lent": true`);
  that is `EmbeddingUnavailable`, so keywords stand in and a caller that keeps an index waits. The refusal is marked
  (`"lent": true`, `CardLent`): the worker answers a status request all through a loan, which is how a picture that
  silences it looks, so PICTURE-SEARCH counts a lent card against no picture. A request may say how long it will wait
  for a lent card (`"wait"`, `nearest` alone, at most `MAX_WAIT` seconds): it is held, not refused, and answered as soon as
  the card is back, before the next loan can begin; when its wait runs out it is refused like any other. Faculty asks to
  wait only where nobody is watching (ASSISTANT-MEMORY "A FOLLOW-UP WAITS FOR A LENT GPU": requirement, 2026-10-11, "I
  want follow-ups to wait on lent cards"); a chat's lookup is never held. A loan names its own end (the
  request's limit and 30 seconds, never more than `MAX_LEND`), so a turn that stops midway costs no more than that: the
  first request after it puts the model back. The model goes back only when the card has room for it and is never left half
  moved, so another program's work is never squeezed by the attempt; without room the worker stays off the card and looks
  again on a later request. Nothing changes what is computed: float32, the same vectors. A worker on the CPU lends nothing;
  with no worker named, none running or one that refuses, the request goes ahead as it did before. Measured that day on a
  worker of its own grown to 2,484 MiB (the live one had reached 2,820), a Letter page at 300 DPI: without the loan the
  request failed (`cudaMalloc failed: out of memory`); with it the page was read in 15 s, the card peaking at 8,860 of
  10,820 MiB, the worker holding 474 MiB meanwhile; giving the card up took about a second, taking it back less, and the vectors were identical.
- **USES.** The Assistant's memory (`AssistantMemory.embedder`): the kept memories nearest a new statement, and nearest a
  request once a course's and term's memories no longer fit. The worker's order is joined with the keyword rule's first
  picks, so a shared word is never lost. Picture search (PICTURE-SEARCH): the pictures, and the words stored about them,
  nearest a description. Past work (PAST-WORK): the Assistant's recorded actions nearest a described situation. A rule
  learned from the instructor's correction of a follow-up's card (ASSISTANT-MEMORY "LEARNED FROM AN EDIT", 2026-10-12) is a
  kept memory like any other: compared with the nearest kept memories as it is kept, and found for a request the same way.
  What was said in recordings (RECORDINGS "Search by meaning", 2026-11-14): the passages of kept transcripts nearest a
  description, embedded by the Inbox worker's capture thread after each of its ticks (twenty seconds apart while passages
  wait) and outside its lock, one text a request, at most eight a turn, so a memory lookup waits behind at most one
  passage (ONE AT A TIME) and nothing of the GPU's shares changes
  (text only: the worker's own quarter). A lent card and a silent worker are a wait that blames no passage.
  The separate follow-up lessons this line once named as "not yet" were withdrawn that day.
- **CHECKS.** A stand-in encoder only: the protocol, the cache and its bound, the limits, refusals that keep the worker
  serving, the mark on a refusal that is about the picture, the picture setting (560, the worker's sizing not undercutting it, `pieces` with the vectors, a worker on another setting not taken), the client's failures, the jail's argv, the lock, that nothing imports a model at import time, and the memory
  using the worker with keywords as the fallback. The loan with a stand-in card: every request refused while lent and the
  card taken back, a loan nobody returns, a card still full, a model never half moved, the bounds of a loan, and Faculty
  borrowing only from a worker it was told of and never failing for it. No model is loaded and the live worker is never reached.
  Recordings' passages (`tests/test_recordings_memos.py`): one text a request as a document, a lent or silent worker blaming
  no passage, a refused passage set aside after three tries, nothing sent with no worker named.
