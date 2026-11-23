"""Desktop companion bridge: Zenko memory drives the fox's mood, and nothing sensitive leaks."""
import json
import stat
import time

import pytest

from foxworx.companion_bridge import (CompanionBridge, WARY_FOR, build_state, derive_mood,
                                      write_state)
from foxworx.pet import DreadFoxPet
from foxworx.zenko_memory import Memory

from tests.test_zenko_memory import fake_embedder

T0 = float(int(time.time()))  # audit rows use the wall clock, so tests start from now


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def pet(store, clock):
    p = DreadFoxPet(store, clock=clock)
    p.hatch('alice')
    return p


@pytest.fixture
def mem(store, clock):
    return Memory(store, embedder=fake_embedder(), clock=clock)


def outcomes(mem, clock, results, step=60):
    for i, ok in enumerate(results):
        clock.t += step
        mem.remember('alice', f'situation {i}', f'action {i}', success=ok)


def test_vitals_track_streaks_rate_and_recovery(mem, clock):
    outcomes(mem, clock, [True, True, False, False, False])
    v = mem.vitals('alice')
    assert v['fail_streak'] == 3 and v['success_rate'] == 0.4 and v['recovered_at'] is None
    outcomes(mem, clock, [True])
    v = mem.vitals('alice')
    assert v['fail_streak'] == 0 and v['recovered_at'] is not None
    broke_at = v['recovered_at']
    outcomes(mem, clock, [True])  # a second win keeps the recovery anchored to the first
    assert mem.vitals('alice')['recovered_at'] == broke_at


def test_vitals_contain_numbers_and_timestamps_only(mem, clock):
    mem.remember('alice', 'smb on 10.9.9.9 CORP-DC01', 'netexec --pass hunter2', success=True)
    blob = json.dumps(mem.vitals('alice'))
    for secret in ('10.9.9.9', 'CORP-DC01', 'hunter2', 'netexec'):
        assert secret not in blob


@pytest.mark.parametrize('vitals,wary,expected', [
    ({}, T0 - 60, 'Wary'),
    ({}, T0 - WARY_FOR - 1, 'Content'),
    ({'fail_streak': 3, 'last_outcome_at': T0 - 60}, None, 'Frustrated'),
    ({'recovered_at': T0 - 60}, None, 'Determined'),
    ({'last_recall_hit': T0 - 60}, None, 'Remembering'),
    ({'success_rate': 0.9, 'outcomes': 10, 'last_outcome_at': T0 - 60}, None, 'Confident'),
    ({'success_rate': 0.9, 'outcomes': 2, 'last_outcome_at': T0 - 60}, None, 'Content'),
])
def test_mood_priority(vitals, wary, expected):
    from foxworx.companion_bridge import _iso
    iso = {k: (_iso(v) if k.endswith(('_at', '_hit')) else v) for k, v in vitals.items()}
    mood, _reason, _until = derive_mood('Content', iso, _iso(wary) if wary else None, T0)
    assert mood == expected


def test_wary_beats_frustrated_and_lapses(store, pet, mem, clock):
    outcomes(mem, clock, [False, False, False])
    store.event('alice', 'rabbit-command-blocked', 'run=1 nmap 172.16.4.20 :: out of scope')
    state = build_state(pet, store, 'alice', now=clock.t)
    assert state['mood'] == 'Wary' and state['mood_until']
    later = build_state(pet, store, 'alice', now=clock.t + WARY_FOR + 1)
    assert later['mood'] == 'Frustrated'


def test_recall_of_proven_lesson_makes_fox_remember(store, pet, mem, clock):
    for _ in range(2):
        mem.remember('alice', 'open ftp 21 anonymous', 'anonymous ftp login', success=True)
    mem.recall('alice', 'ftp 21 anonymous allowed')
    assert build_state(pet, store, 'alice', now=clock.t)['mood'] == 'Remembering'


def test_foxfire_follows_success_rate(store, pet, mem, clock):
    outcomes(mem, clock, [True] * 5)
    bright = build_state(pet, store, 'alice', now=clock.t)['foxfire']
    outcomes(mem, clock, [False] * 10)
    dim = build_state(pet, store, 'alice', now=clock.t)['foxfire']
    assert bright == 1.0 and dim == 0.35


def test_export_is_allow_listed_and_private(store, pet, mem, clock, tmp_path):
    for _ in range(2):
        mem.remember('alice', 'smb on 10.9.9.9 CORP-DC01', 'netexec --pass hunter2', success=True,
                     detail='10.9.9.9 CORP-DC01 hunter2')
    store.event('alice', 'rabbit-command-blocked', 'nmap 10.9.9.9 CORP-DC01 :: hunter2')
    path = tmp_path / 'h1' / 'data' / 'pet.json'
    write_state(path, build_state(pet, store, 'alice', now=clock.t))
    raw = path.read_text()
    for secret in ('10.9.9.9', 'CORP-DC01', 'hunter2', 'netexec', 'nmap'):
        assert secret not in raw
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(path.parent.glob('.pet-*'))  # no temp files left behind
    data = json.loads(raw)
    assert data['hatched'] and data['tails'] >= 1 and data['zenko']['validated'] == 1


def test_bridge_refreshes_on_pet_change_for_configured_owner_only(store, clock, tmp_path):
    path = tmp_path / 'pet.json'
    pet = DreadFoxPet(store, clock=clock)
    pet.on_change = CompanionBridge(pet, store, path, owner='alice', clock=clock).refresh
    pet.hatch('bob')
    assert not path.exists()
    pet.hatch('alice')
    assert json.loads(path.read_text())['hatched'] is True


def test_broken_hook_never_breaks_pet_or_memory(store, clock):
    def boom(_owner):
        raise RuntimeError('desktop gone')
    pet = DreadFoxPet(store, clock=clock)
    pet.on_change = boom
    assert pet.hatch('alice')['hatched']
    mem = Memory(store, embedder=fake_embedder(), clock=clock)
    mem.on_change = boom
    assert mem.remember('alice', 'x', 'y', success=True)['successes'] == 1


def test_consolidation_is_a_stewardship_milestone(store, mem):
    seen = []
    store.pet_observer = lambda owner, action: seen.append(action)
    emb = fake_embedder()('when x try y')
    for i in ('a', 'b'):
        store.put('zenko_lesson', 'alice', {'id': i, 'key': i, 'text': 'When x, try y.', 'embedding': emb,
                                            'successes': 1, 'failures': 0, 'uses': 0, 'validated': False})
    mem.consolidate('alice')
    assert seen == ['zenko-memory-consolidated']


def test_unhatched_owner_exports_minimal_state(store, clock):
    state = build_state(DreadFoxPet(store, clock=clock), store, 'nobody', now=clock.t)
    assert state == {'version': 1, 'exported_at': state['exported_at'], 'hatched': False, 'mood': 'Unhatched'}


def test_mood_source_gives_every_view_the_same_mood(store, pet, clock):
    from foxworx.companion_bridge import companion_mood
    pet.mood_source = lambda owner, snap, when: companion_mood(store, owner, snap, when)
    store.event('alice', 'rabbit-command-blocked', 'nmap 172.16.4.20 :: outside scope')
    snap = pet.snapshot('alice', at=clock.t)          # what /api/pet and the pet chat read
    assert snap['mood'] == 'Wary' and snap['base_mood'] == 'Content'
    assert build_state(pet, store, 'alice', now=clock.t)['mood'] == snap['mood']   # the widget agrees
    assert '172.16.4.20' not in json.dumps(snap)


def test_failing_mood_source_falls_back_to_care_mood(store, pet, clock):
    def boom(*_args):
        raise RuntimeError('memory unavailable')
    pet.mood_source = boom
    assert pet.snapshot('alice', at=clock.t)['mood'] == 'Content'
