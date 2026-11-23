"""Zenko memory: retrieval improves with outcomes, and the companion only ever sees coarse names."""
import json

import pytest

from foxworx.pet import DreadFoxPet
from foxworx.zenko_memory import Memory, cosine, _weight


def fake_embedder(dim: int = 64):
    """Deterministic bag-of-words vector so cosine reflects token overlap (no model needed)."""
    def embed(text: str) -> list[float]:
        vec = [0.0] * dim
        for word in text.lower().split():
            vec[hash(word) % dim] += 1.0
        return vec or [0.0] * dim
    return embed


@pytest.fixture
def mem(store):
    return Memory(store, embedder=fake_embedder())


def test_recall_returns_relevant_lesson(mem):
    mem.remember('alice', 'open smb 445 windows host', 'netexec smb guest enumeration', success=True)
    mem.remember('alice', 'open http 80 apache server', 'gobuster directory brute force', success=True)
    hits = mem.recall('alice', 'found smb 445 on a windows box', k=1)
    assert hits and 'netexec' in hits[0]['text']


def test_proven_lesson_outranks_unproven(mem):
    # Same situation/action tokens, two separate lessons; one accrues successes.
    for _ in range(3):
        mem.remember('alice', 'open ftp 21 anonymous', 'try anonymous ftp login', success=True)
    mem.remember('alice', 'open ftp 21 anonymous', 'brute force ftp credentials', success=False)
    hits = mem.recall('alice', 'ftp 21 anonymous allowed', k=2)
    assert hits[0]['text'].startswith('When open ftp 21 anonymous, try try anonymous')
    assert _weight(3, 0) > _weight(0, 1)


def test_validation_credits_companion_with_coarse_name_only(store):
    seen = []
    store.pet_observer = lambda owner, action: seen.append((owner, action))
    mem = Memory(store, embedder=fake_embedder())
    first = mem.remember('alice', 'exposed redis 6379 no auth', 'redis unauth keyspace read', success=True)
    assert first['validated'] is False and not seen
    mem.remember('alice', 'exposed redis 6379 no auth', 'redis unauth keyspace read', success=True)
    # Crossed VALIDATE_AT: exactly one coarse milestone, no operational text leaked to the pet.
    assert seen == [('alice', 'zenko-lesson-learned')]
    assert 'redis' not in json.dumps(seen)
    # A third success must not re-credit.
    mem.remember('alice', 'exposed redis 6379 no auth', 'redis unauth keyspace read', success=True)
    assert len(seen) == 1


def test_operational_detail_never_reaches_pet_observer(store):
    pet = DreadFoxPet(store)
    pet.hatch('alice')
    store.pet_observer = pet.process_event  # stand in for the async observer worker
    mem = Memory(store, embedder=fake_embedder())
    for _ in range(2):
        mem.remember('alice', 'smb 445 on 10.9.9.9 CORP-DC01', 'netexec --pass hunter2', success=True,
                     detail='10.9.9.9 CORP-DC01 hunter2')
    blob = json.dumps(pet.snapshot('alice'))
    for secret in ('10.9.9.9', 'CORP-DC01', 'hunter2', 'netexec'):
        assert secret not in blob
    assert pet.snapshot('alice')['xp'] > 0  # wisdom credited from the coarse milestone


def test_consolidate_merges_duplicates_and_sums_scores(store):
    mem = Memory(store, embedder=fake_embedder())
    emb = fake_embedder()('when x try y')
    store.put('zenko_lesson', 'alice', {'id': 'a', 'key': 'a', 'text': 'When x, try y.', 'embedding': emb,
                                        'successes': 2, 'failures': 0, 'uses': 0, 'validated': True})
    store.put('zenko_lesson', 'alice', {'id': 'b', 'key': 'b', 'text': 'When x, try y.', 'embedding': emb,
                                        'successes': 1, 'failures': 0, 'uses': 0, 'validated': False})
    result = mem.consolidate('alice')
    assert result['merged'] == 1
    remaining = store.list('zenko_lesson', 'alice', limit=50)
    assert len(remaining) == 1 and remaining[0]['successes'] == 3


def test_reflect_without_model_closes_engagement(store):
    seen = []
    store.pet_observer = lambda owner, action: seen.append(action)
    mem = Memory(store, embedder=fake_embedder())
    mem.remember('alice', 'open ssh 22', 'ssh password spray', success=False, engagement_id='e1')
    import asyncio
    summary = asyncio.run(mem.reflect('alice', 'e1'))
    assert summary['episodes'] == 1
    assert 'zenko-engagement-completed' in seen


def test_cosine_basics():
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine([], [1]) == 0.0
