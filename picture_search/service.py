"""PICTURE SEARCH (docs/decisions/picture-search.md): find a picture Faculty already holds by describing it.

Pictures are embedded by the local worker (EMBEDDING: on this machine, in
float32, nothing leaves it) one per Inbox-worker turn, and a description is matched against them when the instructor or the
Assistant searches. What is indexed is in `sources.py`: scanned pages, documents students send, and course materials.

How a picture looks ranks it only loosely: right and wrong pages score within a hundredth or two of each other (the
requirement, 2026-10-10). So where Faculty already
stores words about a picture (a question's text, a section's title), those words are embedded too and matched first; how a
picture looks is the match for everything else.

A result is only a route Faculty already serves and a few words about where it is. Whether a picture may be shown is
decided here, at search time, from the catalog, by its owner's own rule. A score ranks; it is never evidence of what a
picture shows.
"""
import time
from datetime import datetime, timedelta, timezone

import numpy as np

from faculty_embedding import client as embedding
from faculty_embedding.config import MODEL_ID, MODEL_REVISION, PICTURES
from rosters import connect

from .index import BLANK, NO_WORDS, PictureIndex
from .sources import NotAPicture, Scans, as_picture, blank, is_pdf, pdf_pages, words_hash

# The marks vectors are kept under. Words are the model's alone; a picture's also names how finely the model saw it
# (`PICTURES`: EMBEDDING "PICTURES AT 560 PIECES"), since a picture seen at another setting cannot be compared with these.
MODEL = f"{MODEL_ID}@{MODEL_REVISION}"
IDLE_SECONDS, DOWN_SECONDS = 30, 60  # how long a turn stays cheap once nothing waits, or the worker did not answer
TIDY_SECONDS = 600  # how often an idle turn drops what the index holds of pictures the catalog no longer has
TRIES = 3  # a picture that fails this often in a row is set aside, so it cannot hold every later one behind it
RETRY_DAYS = 7  # and what was set aside is tried again after this long: nothing is lost for good to a passing fault
WORDS_SECONDS = 20  # how long the worker's turn waits for one picture's words to be embedded
# Stored words count as a match only this near the description. Measured 2026-10-10 on the live course text (223 worded
# pictures, this model revision): the best match of each of 8 course topics scored .716 to .794; of 8 descriptions of other
# things (a bubble sheet, a doctor's note, an empty page) the best scored .583 to .719, and only one reached this bar.
# Without it the nearest words always came first, however far. Another model revision needs measuring again.
WORDS_NEAR = 0.70
# Once words have matched, a look-alike fills a place left over only when it is this near. Replayed 2026-10-10 on the live
# index: after "supply and demand" matched four notes pages, the nearest look-alikes were students' attachments at .65 to
# .69 (not it); after "a doctor's note" matched one notes page by chance, absence documents followed at .70 to .74 (it).
# When no words match at all there is no bar: look is then the only answer there is.
# Read again 2026-10-11 with pictures at 560 pieces, among students' files alone: the eight nearest "supply and demand" scored
# .62 to .67 (.63 to .69 at 280) and the eight nearest "a doctor's note" .70 to .74 (the same at 280). The bar stays.
LOOK_NEAR = 0.70


class Unavailable(RuntimeError):
    """Picture search cannot answer now (no embedding worker); the words are for the instructor."""


NOT_ANSWERING = "Picture search is not answering right now. Try again in a moment."


class PictureSearch:
    def __init__(self, path, inbox, folder, sources=None):
        self.path = path
        self.index = PictureIndex(folder)
        self.sources = list(sources) if sources is not None else [Scans(inbox)]
        self._wait_until, self._tidy_after, self._failed = 0.0, 0.0, {}

    # -- what there is to index --------------------------------------------------------------------------------------------
    def waiting(self):
        """[(source, unit)] still to do, in the sources' order: a unit waits when its picture has no vector of this model (or
        was drawn again), or when the words stored about it have none (or changed, or are gone)."""
        aside = (datetime.now(timezone.utc) - timedelta(days=RETRY_DAYS)).isoformat()
        with connect(self.path) as db:
            listed = [(source, source.units(db)) for source in self.sources]
        found = []
        for source, units in listed:
            done, worded = self.index.done(source.name, PICTURES, aside), self.index.texts_done(source.name, MODEL, aside)
            for unit in units:
                if done.get(unit["ref"]) != unit["sha256"] or worded.get(unit["ref"], "") != (words_hash(unit["words_key"]) if unit["words_key"] else ""):
                    found.append((source, unit))
        return found

    def _tidy(self):
        """Drop what the index holds of pictures the catalog no longer has (a scan removed for good, or started over; a
        question or a document deleted): a cache keeps nothing of work Faculty no longer keeps. What is only hidden (a scan
        deleted in the Inbox) is still kept, and so is its entry; the search leaves it out.

        It runs only when nothing waits, so everything is in under the marks of now: what another model revision made, or
        pictures seen at another setting, is never searched again and is forgotten here too."""
        if time.monotonic() < self._tidy_after:
            return
        self._tidy_after = time.monotonic() + TIDY_SECONDS
        self.index.keep_only(PICTURES, MODEL)
        for source in self.sources:
            held = self.index.refs(source.name)
            if held:
                with connect(self.path) as db:
                    kept = source.existing(db)
                self.index.drop(source.name, held - kept)

    # -- the Inbox worker's turn -------------------------------------------------------------------------------------------
    def process_one(self):
        """Embed one waiting unit: a picture (or a few pages of one PDF) and the words stored about it. Cheap when nothing
        waits; never holds a lock while the model works; one picture per request, so the Assistant's memory lookups are never
        kept waiting behind a batch."""
        if not embedding.configured() or time.monotonic() < self._wait_until:
            return False
        waiting = self.waiting()
        if not waiting:
            self._tidy()
            self._wait_until = time.monotonic() + IDLE_SECONDS
            return False
        source, unit = waiting[0]
        name, ref, digest = source.name, unit["ref"], unit["sha256"]
        marked = words_hash(unit["words_key"]) if unit["words_key"] else ""

        def aside(reason):
            self.index.skip(name, ref, digest, PICTURES, reason)
            if marked:
                self.index.skip_text(name, ref, marked, MODEL, reason)

        def keep(item, vector, words):
            self.index.add(name, item, digest, PICTURES, vector)
            self.index.set_text(name, item, marked if words is not None else "", MODEL, words)

        def leave(item, reason):  # one page of a unit that is not kept: neither its picture nor its words
            self.index.skip(name, item, digest, PICTURES, reason)
            self.index.set_text(name, item, "", MODEL, None)

        try:
            drawn = source.read(unit)
        except NotAPicture as refused:  # a video, a document: nothing to embed, and no later try can change that
            aside(str(refused))
            return True
        except Exception as failure:  # noqa: BLE001 - storage busy, or a file that is missing, fails its checksum or cannot be drawn
            return self._trouble((name, ref), "The picture's file could not be read: " + type(failure).__name__, lambda: True, aside)
        own, left = None, ""
        try:
            for item, content, words in drawn:
                # A page with nothing on it is never indexed: it looks like nothing in particular, and so like anything.
                reason = BLANK if blank(content) else ""
                if not reason:
                    try:
                        vector = embedding.picture_vectors([content])[0]
                    except embedding.PictureRefused as refused:  # the worker read it and will not take it: unreadable, or too large
                        reason = str(refused)
                if reason:  # this page is left out; the unit's other pages are still read
                    if item == ref:
                        left = reason
                    else:
                        leave(item, reason)
                    continue
                worded = embedding.text_vectors([words], "document", timeout=WORDS_SECONDS)[0] if words else None
                if item == ref:
                    own = (vector, worded)
                else:
                    keep(item, vector, worded)
        except (embedding.CardLent, embedding.OtherSetting):
            # The worker has lent the GPU to the local grading model (EMBEDDING "LENDS THE GPU"), or was started before the
            # picture setting changed and sees pictures at the old one. It answers a status request all the while, which would
            # otherwise read as this picture silencing it: wait, and count nothing against the picture.
            self._wait_until = time.monotonic() + DOWN_SECONDS
            return False
        except embedding.EmbeddingUnavailable:
            # No answer. Usually the worker is down or busy, which is no picture's fault: only a worker that still answers a
            # status request is one this picture silenced.
            return self._trouble((name, ref), "The embedding worker did not answer for this picture.", self._answers, aside)
        if own is None:
            aside(left or "The file held no picture.")
            return True
        keep(ref, *own)  # last: a unit counts as done only once all of it is in
        if marked and own[1] is None:
            self.index.skip_text(name, ref, marked, MODEL, NO_WORDS)
        self._failed = {}
        return True

    def _trouble(self, key, reason, its_own, aside):
        """A picture that could not be read or got no answer: wait, and try it again. After TRIES in a row, when `its_own()`
        says the trouble is this picture's, it is set aside (and tried again in RETRY_DAYS)."""
        self._failed = {key: self._failed.get(key, 0) + 1}
        if self._failed[key] >= TRIES and its_own():
            aside(reason)
            self._failed = {}
            return True
        self._wait_until = time.monotonic() + DOWN_SECONDS
        return False

    @staticmethod
    def _answers():
        try:
            return bool(embedding.status())
        except embedding.EmbeddingUnavailable:
            return False

    # -- searching ---------------------------------------------------------------------------------------------------------
    def status(self):
        counts = self.index.counts(PICTURES)
        return {"available": embedding.configured(), **counts, "waiting": len(self.waiting()) if embedding.configured() else None}

    def _nearest(self, refs, scores, allowed, want, matched, seen, floor=-1.0):
        """The `want` nearest of `refs` (those `allowed`, scoring at least `floor`) that may be shown now, best first. Some
        of the nearest may be hidden now (a deleted scan, a removed document), so it looks a little further than asked. Of
        results that are the same thing (`same`: one printed page in several versions, or its many scanned copies) only
        the nearest is kept."""
        found = []
        if want < 1 or not refs:
            return found
        inside = np.flatnonzero(np.fromiter((allowed(ref) for ref in refs), dtype=bool, count=len(refs)) & (scores >= floor))
        order = [int(index) for index in inside[np.argsort(-scores[inside])][:want * 8]]
        by_name = {source.name: source for source in self.sources}
        for start in range(0, len(order), want * 2):
            part = order[start:start + want * 2]
            shown = {}
            with connect(self.path) as db:
                for name in {refs[index][0] for index in part}:
                    for ref, hit in by_name[name].shown(db, [refs[index][1] for index in part if refs[index][0] == name]).items():
                        shown[(name, ref)] = {"source": name, "kind": by_name[name].kind, "id": ref, **hit}
            for index in part:
                hit = shown.get(refs[index])
                if hit and len(found) < want and hit.get("same") not in seen:
                    seen.add(hit.get("same"))
                    seen.discard(None)
                    found.append({**hit, "matched": matched, "score": round(float(scores[index]), 3)})
            if len(found) >= want:
                break
        return found

    def _scope(self, limit, sources):
        """(limit kept to 1..24, the names of the sources to search); refuses an unknown kind, or any search while the embedding
        service is not set up."""
        limit = min(max(int(limit), 1), 24)
        known = {source.name for source in self.sources}
        names = known if not sources else {name for name in sources if name in known}
        if sources and not names:
            raise ValueError("Search one of: " + ", ".join(sorted(known)) + ".")
        if not embedding.configured():
            raise Unavailable("Picture search needs the local embedding service, which is not set up here.")
        return limit, names

    @staticmethod
    def _with_scans(results, copies):
        """Take each result's `same` mark off and, where Faculty holds scanned copies of that thing, say how many (`scans`)."""
        for hit in results:
            held = copies.get(hit.pop("same", None))
            if held:
                hit["scans"] = {"count": held["count"], "link": held["link"]}

    def search(self, text, limit=12, sources=None):
        """What is nearest a description, best first: {"query", "results": [{kind, image, thumb, open, label, where, link,
        matched, score, scans?}], "searched"}. `sources` names the kinds to search; all of them by default.

        What Faculty has words about is matched by those words (`matched` "words": only words at least WORDS_NEAR the
        description), printed pages first; a scanned page is found through the page it was printed from. What Faculty has no
        words about (an unidentified scan, a student's file) is matched by how it looks ("look") and fills what is left,
        when it is at least LOOK_NEAR. Only when no words match at all is everything matched by look, however far. A
        printed page and its scanned copies are one result, which says how many scans there are (`scans`)."""
        text = " ".join(str(text or "").split())
        if not text:
            raise ValueError("Describe what the picture shows.")
        if len(text) > 500:
            raise ValueError("Describe the picture in at most 500 characters.")
        limit, names = self._scope(limit, sources)
        try:
            asked = embedding.text_vectors([text], "query")[0]
        except embedding.EmbeddingUnavailable:
            raise Unavailable(NOT_ANSWERING) from None
        refs, matrix = self.index.matrix(PICTURES)
        worded, words = self.index.text_matrix(MODEL)
        with connect(self.path) as db:
            same = {(source.name, ref): key for source in self.sources for ref, key in source.same(db).items()}
            copies = {key: {**held, "source": source.name} for source in self.sources if source.name in names
                      for key, held in source.gathered(db).items()}
        has_words = set(worded)
        worded_things = {same[ref] for ref in has_words if ref in same}
        look = matrix @ asked if refs else None
        seen, by_words = set(), []
        if worded:
            near = words @ asked
            # Words about a thing whose copies are searched count even when the thing itself is not asked for: a scan is
            # found through the page it was printed from.
            found = self._nearest(worded, near, lambda ref: ref[0] in names or same.get(ref) in copies, limit * 3, "words", seen, WORDS_NEAR)
            found = [hit for hit in (hit if hit["source"] in names else self._copy(hit, refs, look, same) for hit in found) if hit]
            # The nearest of each kind first (a printed page, a bank picture, a notes page), then the second nearest of
            # each, so the many pictures of one kind cannot crowd out the one page of another; then the nearest of any.
            turn, rank = {}, {}
            for place, hit in enumerate(found):
                turn[place] = rank[hit["source"]] = rank.get(hit["source"], 0) + 1
            kept = [found[place] for place in sorted(turn, key=lambda place: (min(turn[place], 3), place))][:limit]
            by_words = sorted(kept, key=lambda hit: ("same" not in hit, -hit["score"]))  # printed pages first
        if by_words:
            # Words matched: what is left goes only to what Faculty has no words about. A page it can read is not guessed at.
            taken = {(hit["source"], hit["id"]) for hit in by_words}
            by_look = self._nearest(refs, look, lambda ref: (ref[0] in names and ref not in has_words and ref not in taken
                                                             and same.get(ref) not in worded_things),
                                    limit - len(by_words), "look", seen, LOOK_NEAR)
        else:
            by_look = self._nearest(refs, look, lambda ref: ref[0] in names, limit, "look", seen)
        results = by_words + by_look
        self._with_scans(results, copies)
        return {"query": text, "results": results, "searched": sum(1 for ref in refs if ref[0] in names)}

    def like(self, content, limit=12, sources=None, page=1, timeout=20):
        """What looks nearest a picture the instructor hands over (a file of theirs, or one Faculty serves; a PDF by one of
        its pages), best first: {"results": [...], "searched"}, every result `matched` "look". Picture against picture is a
        far stronger likeness than words against a picture: on the live index every blank printed page's nearest scan was a
        copy of that page. The picture is embedded on this machine and kept nowhere."""
        limit, names = self._scope(limit, sources)
        try:
            if is_pdf(content):
                drawn, total = pdf_pages(content, [int(page)])
                if int(page) not in drawn:
                    raise ValueError(f"This PDF has {total} page{'' if total == 1 else 's'}.")
                picture = drawn[int(page)]
            else:
                picture = as_picture(content)
        except NotAPicture as refused:
            raise ValueError(str(refused)) from None
        try:
            asked = embedding.picture_vectors([picture], timeout=timeout)[0]
        except embedding.PictureRefused as refused:
            raise ValueError(str(refused)) from None
        except embedding.EmbeddingUnavailable:
            raise Unavailable(NOT_ANSWERING) from None
        refs, matrix = self.index.matrix(PICTURES)
        with connect(self.path) as db:
            copies = {key: held for source in self.sources if source.name in names for key, held in source.gathered(db).items()}
        results = self._nearest(refs, matrix @ asked if refs else None, lambda ref: ref[0] in names, limit, "look", set())
        self._with_scans(results, copies)
        return {"results": results, "searched": sum(1 for ref in refs if ref[0] in names)}

    def _copy(self, hit, refs, look, same):
        """A thing matched by its words but not asked for itself (a printed page when only scans are searched), as the
        nearest of its copies that may be shown: the scan stands for the page, with the page's match."""
        thing = hit.get("same")
        if not thing or not refs:
            return None
        copies = self._nearest(refs, look, lambda ref: same.get(ref) == thing and ref[0] != hit["source"], 1, "words", set())
        return {**copies[0], "score": hit["score"]} if copies else None
