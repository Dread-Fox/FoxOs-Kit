"""Desktop DreadFox bridge: export the companion's state for the FoxOS desktop widget.

The widget (``~/h1/dreadfox_widget.py``) reads a small JSON file. This module builds that file
from the pet snapshot plus Zenko memory *vitals* and works out a mood from them.

Privacy boundary: the exported file is built from an explicit allow-list of numbers, timestamps,
mood words, the pet's own built-in narration and the operator-chosen pet name. Lesson text,
episodes, targets, commands, tool output and audit details are never read into it. Audit rows are
consulted only for the timestamp of a coarse action name.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .zenko_memory import Memory

LOG = logging.getLogger(__name__)
VERSION = 1

# Audit action names that make the fox wary. Only their timestamps are read.
WARY_ACTIONS = ('rabbit-command-blocked',)

# How long each memory-driven mood lasts after its triggering signal (seconds).
WARY_FOR = 15 * 60
DETERMINED_FOR = 30 * 60
REMEMBERING_FOR = 10 * 60
CONFIDENT_FOR = 2 * 3600
FRUSTRATED_FOR = 6 * 3600
DREAM_AFTER = 24 * 3600

PET_KEYS = ('name', 'stage', 'tails', 'level', 'xp', 'xp_level_start', 'xp_next', 'next_tail',
            'age_days', 'streak_days', 'energy', 'bond', 'curiosity', 'mood')
VITAL_KEYS = ('lessons', 'validated', 'outcomes', 'success_rate', 'fail_streak')


def _epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (TypeError, ValueError):
        return None


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec='seconds')


def last_action_at(store, owner: str, actions: tuple[str, ...]) -> str | None:
    """Newest audit timestamp for any of ``actions``. Never reads the details column."""
    marks = ','.join('?' * len(actions))
    with store.db() as db:
        row = db.execute(f'SELECT MAX(ts) AS ts FROM audit WHERE owner=? AND action IN ({marks})',  # nosec B608 - placeholders only
                         (owner, *actions)).fetchone()
    return row['ts'] if row else None


def derive_mood(base_mood: str, vitals: dict, wary_at: str | None, now: float) -> tuple[str, str, float | None]:
    """Return ``(mood, reason, until_epoch)``. Memory moods outrank the pet's care-based mood.

    ``until`` is when the mood should lapse back to ``base_mood``; None means it holds until the
    next export.
    """
    def since(iso):
        ts = _epoch(iso)
        return None if ts is None else now - ts

    wary = since(wary_at)
    if wary is not None and wary < WARY_FOR:
        return 'Wary', 'blocked', now - wary + WARY_FOR
    last_outcome = since(vitals.get('last_outcome_at'))
    if vitals.get('fail_streak', 0) >= 3 and last_outcome is not None and last_outcome < FRUSTRATED_FOR:
        return 'Frustrated', 'fail_streak', now - last_outcome + FRUSTRATED_FOR
    recovered = since(vitals.get('recovered_at'))
    if recovered is not None and recovered < DETERMINED_FOR:
        return 'Determined', 'recovered', now - recovered + DETERMINED_FOR
    recall = since(vitals.get('last_recall_hit'))
    if recall is not None and recall < REMEMBERING_FOR:
        return 'Remembering', 'recall', now - recall + REMEMBERING_FOR
    rate = vitals.get('success_rate')
    if (rate is not None and rate >= 0.8 and vitals.get('outcomes', 0) >= 5
            and last_outcome is not None and last_outcome < CONFIDENT_FOR):
        return 'Confident', 'success_rate', now - last_outcome + CONFIDENT_FOR
    return base_mood, 'care', None


MOOD_KEYS = ('base_mood', 'mood', 'mood_reason', 'mood_until', 'foxfire', 'zenko')


def companion_mood(store, owner: str, snap: dict, now: float) -> dict:
    """Memory-driven mood fields for a hatched snapshot: the one mood every view shows.

    Installed as ``DreadFoxPet.mood_source`` so the web sanctuary, the pet chat and the desktop
    widget agree. Returns aggregate fields only: mood words, numbers and timestamps.
    """
    base = snap.get('base_mood') or snap['mood']
    vitals = Memory(store, embedder=lambda _text: []).vitals(owner)
    mood, reason, until = derive_mood(base, vitals, last_action_at(store, owner, WARY_ACTIONS), now)
    rate = vitals.get('success_rate')
    return {
        'base_mood': base,
        'mood': mood,
        'mood_reason': reason,
        'mood_until': _iso(until) if until else None,
        # Foxfire brightness tracks recent success; neutral until enough outcomes exist.
        'foxfire': round(0.35 + 0.65 * rate, 2) if rate is not None else 0.7,
        'zenko': {k: vitals.get(k) for k in VITAL_KEYS},
    }


def build_state(pet, store, owner: str, *, now: float | None = None) -> dict:
    """The full allow-listed widget state for one owner."""
    now = time.time() if now is None else now
    snap = pet.snapshot(owner, at=now)
    if not snap.get('hatched'):
        return {'version': VERSION, 'exported_at': _iso(now), 'hatched': False, 'mood': 'Unhatched'}
    if 'base_mood' not in snap:  # the pet has no mood_source hook installed: derive it here
        snap = {**snap, **companion_mood(store, owner, snap, now)}
    memories = snap.get('memories') or []
    milestone = next((m for m in memories if m.get('xp', 0) > 0), None)
    state = {
        'version': VERSION,
        'exported_at': _iso(now),
        'hatched': True,
        **{k: snap.get(k) for k in PET_KEYS},
        **{k: snap.get(k) for k in MOOD_KEYS},
        'last_active_at': snap.get('last_active_at'),
        'dream_after_s': DREAM_AFTER,
        'badges': [b['name'] for b in snap.get('badges', [])],
        'last_milestone': ({'at': milestone['at'], 'kind': milestone['kind'],
                            'note': milestone['note'], 'xp': milestone['xp']} if milestone else None),
    }
    return state


def write_state(path: Path, state: dict) -> None:
    """Atomic, owner-only write so the widget never reads a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix='.pet-', suffix='.json', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as fh:
            json.dump(state, fh, indent=1)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class CompanionBridge:
    """Refreshes the desktop state file on pet/memory changes. Best-effort by design."""

    def __init__(self, pet, store, path: Path, *, owner: str = '', clock=time.time):
        self.pet, self.store, self.path, self.owner, self.clock = pet, store, Path(path), owner, clock

    def refresh(self, owner: str) -> bool:
        # With a configured owner, only that operator's fox is shown on this desktop.
        if self.owner and owner != self.owner:
            return False
        try:
            write_state(self.path, build_state(self.pet, self.store, owner, now=self.clock()))
            return True
        except Exception:
            LOG.exception('DreadFox desktop export failed')
            return False


def main(argv: list[str] | None = None) -> int:
    """One-shot export: ``python -m foxworx.companion_bridge --owner <operator> [--file PATH]``."""
    from .config import Settings
    from .pet import DreadFoxPet
    from .store import Store

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument('--owner', help='operator id (defaults to FOXWORX_COMPANION_OWNER)')
    parser.add_argument('--file', help='state file (defaults to FOXWORX_COMPANION_FILE)')
    args = parser.parse_args(argv)
    cfg = Settings.load()
    owner = args.owner or cfg.companion_owner
    target = args.file or cfg.companion_file
    if not owner or not target:
        parser.error('an owner and a state file are required (flags or FOXWORX_COMPANION_* settings)')
    store = Store(cfg.state)
    ok = CompanionBridge(DreadFoxPet(store), store, Path(target).expanduser(), owner=owner).refresh(owner)
    print(f'wrote {target}' if ok else 'export failed; see log')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
