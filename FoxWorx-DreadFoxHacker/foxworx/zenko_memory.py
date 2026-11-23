"""Zenko self-improving memory: episodic recall and distilled lessons with outcome weighting.

Operational content (coarse observation, tool, argv summary, outcome) lives only in the
actor-bound records store, exactly like managed jobs and proposals already do. The DreadFox
companion is never given this content: it receives only coarse milestone action *names*
through the existing non-blocking audit observer in ``store.event``.

Embeddings come from a local LM Studio server (``nomic-embed-text-v1.5`` by default) over loopback;
retrieval is in-process cosine similarity, so no external vector database and no network
egress beyond the already-approved local inference endpoint are introduced. The store stays
bounded through ``consolidate`` so it does not grow without limit.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
import urllib.request
from typing import Callable

EPISODE = 'zenko_episode'      # raw per-step experience, actor-bound
LESSON = 'zenko_lesson'        # distilled, reusable playbook with an outcome score
STATE = 'zenko_state'          # per-actor timestamps for companion signals (no content)

# A lesson is "validated" (worth crediting the companion once) when its net score first
# reaches this many more successes than failures. Repeated spam cannot re-trigger it.
VALIDATE_AT = 2
MAX_LESSONS = 400              # per actor; consolidate() prunes beyond this
DUP_COSINE = 0.94             # merge lessons at/above this similarity
EMBED_DIM_MAX = 4096          # reject absurd vectors defensively

_WS = re.compile(r'\s+')
_SECRETISH = re.compile(r'\b[A-Za-z0-9+/_-]{24,}\b')


def _clean(text: str, limit: int = 400) -> str:
    """Collapse whitespace and drop long high-entropy blobs that look like secrets/hashes."""
    text = _SECRETISH.sub('…', str(text))
    return _WS.sub(' ', text).strip()[:limit]


def lmstudio_embed(text: str, *, model: str = 'text-embedding-nomic-embed-text-v1.5@q8_0',
                   url: str = 'http://127.0.0.1:1234/v1/embeddings', timeout: float = 20.0) -> list[float]:
    """Embed via LM Studio's OpenAI-compatible endpoint over loopback. Raises on transport/shape error."""
    body = json.dumps({'model': model, 'input': text[:8000]}).encode()
    request = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - fixed loopback URL
        data = json.loads(response.read()).get('data') or [{}]
    vector = data[0].get('embedding') or []
    if not vector or len(vector) > EMBED_DIM_MAX:
        raise ValueError('Embedding endpoint returned an empty or oversized vector')
    return [float(x) for x in vector]


def ollama_embed(text: str, *, model: str = 'nomic-embed-text',
                 url: str = 'http://127.0.0.1:11434/api/embeddings', timeout: float = 20.0) -> list[float]:
    """Embed via a local Ollama model over loopback. Raises on transport/shape error."""
    body = json.dumps({'model': model, 'prompt': text[:8000]}).encode()
    request = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - fixed loopback URL
        vector = json.loads(response.read()).get('embedding') or []
    if not vector or len(vector) > EMBED_DIM_MAX:
        raise ValueError('Embedding endpoint returned an empty or oversized vector')
    return [float(x) for x in vector]


def _norm(vector: list[float]) -> float:
    return math.sqrt(sum(x * x for x in vector)) or 1.0


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True)) / (_norm(a) * _norm(b))


def _weight(successes: int, failures: int) -> float:
    """Outcome multiplier: proven lessons rank higher, repeatedly-failing ones sink."""
    return max(0.2, min(3.0, 1.0 + 0.25 * (successes - failures)))


class Memory:
    """Self-improving retrieval memory bound to a single store.

    ``embedder`` maps text to a vector (injected for tests/offline). ``complete`` is an
    optional async model call ``(messages) -> text`` used only by :meth:`reflect`; when
    absent, reflection simply closes the engagement and credits the milestone.
    """

    def __init__(self, store, *, embedder: Callable[[str], list[float]] | None = None,
                 complete: Callable | None = None, clock: Callable[[], float] = time.time):
        self.store = store
        self.embed = embedder or lmstudio_embed
        self.complete = complete
        self.clock = clock
        # Optional best-effort hook ``(owner) -> None``; the companion bridge uses it to refresh
        # its aggregate-only desktop state. It never receives lesson or episode content.
        self.on_change: Callable[[str], None] | None = None

    def _changed(self, owner: str) -> None:
        hook = self.on_change
        if hook is not None:
            try:
                hook(owner)
            except Exception:  # nosec B110 - a desktop companion must never break memory
                pass

    # -- capture -----------------------------------------------------------------
    @staticmethod
    def _key(observation: str, action: str) -> str:
        return hashlib.sha256(f'{observation.lower()}|{action.lower()}'.encode()).hexdigest()[:16]

    def remember(self, owner: str, observation: str, action: str, *, success: bool,
                 engagement_id: str = '', detail: str = '') -> dict:
        """Record one experience and fold it into its distilled lesson.

        ``observation`` is the coarse situation (e.g. "open smb 445 windows host"); ``action``
        is what was tried (e.g. "netexec smb guest enumeration"). Returns the lesson record.
        Emits the ``zenko-lesson-learned`` companion milestone the first time a lesson becomes
        validated — a coarse action name only, never the observation/action text.
        """
        observation, action, detail = _clean(observation, 200), _clean(action, 200), _clean(detail)
        key = self._key(observation, action)
        self.store.put(EPISODE, owner, {'created': self._stamp(), 'engagement_id': engagement_id, 'key': key,
                                        'observation': observation, 'action': action,
                                        'success': bool(success), 'detail': detail})
        lesson = self._lesson_by_key(owner, key)
        if lesson is None:
            text = f'When {observation}, try {action}.'
            lesson = {'id': key, 'key': key, 'observation': observation, 'action': action,
                      'text': text, 'embedding': self.embed(text), 'successes': 0, 'failures': 0,
                      'uses': 0, 'validated': False, 'last_used': ''}
        if success:
            lesson['successes'] += 1
        else:
            lesson['failures'] += 1
        newly_validated = (not lesson['validated']
                           and lesson['successes'] - lesson['failures'] >= VALIDATE_AT)
        if newly_validated:
            lesson['validated'] = True
        self.store.put(LESSON, owner, lesson)
        if newly_validated:
            # Coarse milestone only; store.event strips all detail before the pet observer.
            self.store.event(owner, 'zenko-lesson-learned', 'a tactic proved reliable')
        self._changed(owner)
        return lesson

    def _lesson_by_key(self, owner: str, key: str) -> dict | None:
        return self.store.get(LESSON, key, owner)

    # -- recall ------------------------------------------------------------------
    def recall(self, owner: str, query: str, *, k: int = 5, min_similarity: float = 0.2) -> list[dict]:
        """Return the top-k lessons for a situation, ranked by similarity × outcome weight."""
        lessons = self.store.list(LESSON, owner, limit=MAX_LESSONS)
        if not lessons:
            return []
        q = self.embed(query)
        scored = []
        for lesson in lessons:
            sim = cosine(q, lesson.get('embedding', []))
            if sim < min_similarity:
                continue
            rank = sim * _weight(lesson.get('successes', 0), lesson.get('failures', 0))
            scored.append((rank, sim, lesson))
        scored.sort(key=lambda row: row[0], reverse=True)
        top = []
        proven = False
        for rank, sim, lesson in scored[:k]:
            proven = proven or lesson.get('successes', 0) - lesson.get('failures', 0) >= VALIDATE_AT
            lesson['uses'] = lesson.get('uses', 0) + 1
            lesson['last_used'] = self._stamp()
            self.store.put(LESSON, owner, lesson)
            top.append({'text': lesson['text'], 'similarity': round(sim, 3),
                        'successes': lesson.get('successes', 0), 'failures': lesson.get('failures', 0),
                        'rank': round(rank, 3)})
        if proven:
            state = self._state(owner)
            state['last_recall_hit'] = self._stamp()
            self.store.put(STATE, owner, state)
            self._changed(owner)
        return top

    def recall_prompt(self, owner: str, query: str, *, k: int = 5) -> str:
        """A short, model-ready memory block, or '' when nothing relevant is known."""
        hits = self.recall(owner, query, k=k)
        if not hits:
            return ''
        lines = [f"- {h['text']} (proven {h['successes']}×, failed {h['failures']}×)" for h in hits]
        return 'Relevant lessons from past authorized work:\n' + '\n'.join(lines)

    # -- maintenance -------------------------------------------------------------
    def consolidate(self, owner: str) -> dict:
        """Merge near-duplicate lessons and prune stale, net-negative ones. Bounded + idempotent."""
        lessons = self.store.list(LESSON, owner, limit=MAX_LESSONS * 2)
        merged = 0
        kept: list[dict] = []
        for lesson in lessons:
            emb = lesson.get('embedding', [])
            dup = next((other for other in kept if cosine(emb, other.get('embedding', [])) >= DUP_COSINE), None)
            if dup is not None:
                dup['successes'] += lesson.get('successes', 0)
                dup['failures'] += lesson.get('failures', 0)
                dup['uses'] = dup.get('uses', 0) + lesson.get('uses', 0)
                dup['validated'] = dup['validated'] or lesson.get('validated', False)
                self.store.delete(LESSON, lesson['id'], owner)
                self.store.put(LESSON, owner, dup)
                merged += 1
            else:
                kept.append(lesson)
        pruned = 0
        for lesson in kept:
            stale = lesson.get('successes', 0) - lesson.get('failures', 0) <= -3 and lesson.get('uses', 0) == 0
            if stale:
                self.store.delete(LESSON, lesson['id'], owner)
                pruned += 1
        if merged or pruned:
            self.store.event(owner, 'zenko-memory-consolidated', 'tidied lessons')
            self._changed(owner)
        return {'merged': merged, 'pruned': pruned, 'remaining': len(kept) - pruned}

    # -- vitals (aggregate-only signals for the companion) -----------------------
    @staticmethod
    def _state_id(owner: str) -> str:
        # Record ids are global, so the per-owner state id is derived from the owner.
        return 'state-' + hashlib.sha256(owner.encode()).hexdigest()[:16]

    def _state(self, owner: str) -> dict:
        return self.store.get(STATE, self._state_id(owner), owner) or {'id': self._state_id(owner)}

    def vitals(self, owner: str, *, window: int = 20) -> dict:
        """Numbers and timestamps only, never lesson/episode text: safe to hand to the companion.

        ``success_rate`` covers the last 10 outcomes (None until 3 exist). ``fail_streak`` counts
        consecutive failures ending at the newest outcome. ``recovered_at`` is set when the newest
        success run began by breaking a streak of 3+ failures.
        """
        episodes = self.store.list(EPISODE, owner, limit=window)  # newest first
        outcomes = [bool(e.get('success')) for e in episodes]
        recent = outcomes[:10]
        fail_streak = next((i for i, ok in enumerate(outcomes) if ok), len(outcomes))
        recovered_at = None
        wins = next((i for i, ok in enumerate(outcomes) if not ok), len(outcomes))  # newest success run
        if wins:
            slump = next((i for i, ok in enumerate(outcomes[wins:]) if ok), len(outcomes) - wins)
            if slump >= 3:
                # The success that broke the slump: the oldest of the current winning run.
                recovered_at = episodes[wins - 1].get('created')
        lessons = self.store.list(LESSON, owner, limit=MAX_LESSONS)
        return {
            'lessons': len(lessons),
            'validated': sum(1 for x in lessons if x.get('validated')),
            'outcomes': len(recent),
            'success_rate': round(sum(recent) / len(recent), 2) if len(recent) >= 3 else None,
            'fail_streak': fail_streak,
            'recovered_at': recovered_at,
            'last_outcome_at': episodes[0].get('created') if episodes else None,
            'last_recall_hit': self._state(owner).get('last_recall_hit'),
        }

    # -- reflection --------------------------------------------------------------
    async def reflect(self, owner: str, engagement_id: str, *, route: dict | None = None) -> dict:
        """Close out an engagement: optional model distillation, then credit the milestone.

        When a ``complete`` callable and ``route`` are provided, ask the model to turn the raw
        episodes into a few extra general lessons. Model output is treated as untrusted text and
        folded in through the same scored path as any other lesson.
        """
        episodes = [e for e in self.store.list(EPISODE, owner, limit=500)
                    if e.get('engagement_id') == engagement_id]
        distilled = 0
        if self.complete and route and episodes:
            trace = '\n'.join(f"- {'ok ' if e['success'] else 'no '}{e['observation']} -> {e['action']}"
                              for e in episodes[:120])
            messages = [
                {'role': 'system', 'content': 'You distill penetration-test experience into terse, '
                 'general, reusable lessons. Reply ONLY with a JSON array of objects '
                 '{"observation": "...", "action": "...", "worked": true/false}. No prose.'},
                {'role': 'user', 'content': f'Engagement trace:\n{trace}\n\nUp to 6 lessons:'},
            ]
            try:
                raw = await self.complete(route, messages)
                for item in json.loads(_json_slice(raw))[:6]:
                    self.remember(owner, item.get('observation', ''), item.get('action', ''),
                                  success=bool(item.get('worked', True)), engagement_id=engagement_id)
                    distilled += 1
            except Exception:  # nosec B110 - reflection is best-effort; never block engagement close
                distilled = 0
        self.consolidate(owner)
        self.store.event(owner, 'zenko-engagement-completed', 'reviewed an engagement and learned from it')
        self._changed(owner)
        return {'episodes': len(episodes), 'distilled': distilled}

    def _stamp(self) -> str:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(timespec='seconds')


def _json_slice(text: str) -> str:
    """Extract the first JSON array from a model reply; defends against surrounding prose."""
    start, end = text.find('['), text.rfind(']')
    if start == -1 or end <= start:
        raise ValueError('No JSON array in model reply')
    return text[start:end + 1]
