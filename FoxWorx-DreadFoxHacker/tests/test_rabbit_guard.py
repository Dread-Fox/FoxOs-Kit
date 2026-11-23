"""v3.4.0: Rabbit Hole installed-CLI guardrails enforce scope and deny dangerous commands."""
import pytest
from fastapi import HTTPException

from foxworx.models import ProposalSpec, RabbitSpec
from foxworx.rabbit_guard import check_command
from tests.test_autonomous_cli import OWNER, action, finish, settle, setup_cli  # noqa: F401  (fixture)

INC = ['10.0.0.0/24', 'example.com', '*.example.com', 'https://app.example.org/portal']
EXC = ['10.0.0.99', 'admin.example.com']


@pytest.mark.parametrize('tool,args', [
    ('nmap', ['-sV', '-p', '80,443', '10.0.0.5']),
    ('nmap', ['-oX', 'scan.xml', '10.0.0.5,10.0.0.6']),
    ('nmap', ['--script', 'http-title', '10.0.0.0/28']),
    ('curl', ['-sS', 'https://www.example.com/robots.txt']),
    ('curl', ['https://app.example.org/portal/login']),
    ('gobuster', ['dir', '-u', 'http://example.com', '-w', '/usr/share/wordlists/dirb/common.txt']),
    ('hydra', ['-l', 'admin', '-P', 'passwords.lst', 'ssh://10.0.0.7']),
    ('whatweb', ['--aggression=1', 'http://example.com/']),
    ('curl', ['example.com/robots.txt']),
    ('nmap', ['--top-ports', '100', '--max-rate', '10', '--host-timeout', '0.5', '10.0.0.5:443']),
])
def test_in_scope_commands_pass(tool, args):
    assert check_command(tool, args, INC, EXC, workbench_port=8765) == []


@pytest.mark.parametrize('tool,args,reason', [
    ('nmap', ['8.8.8.8'], 'outside the engagement scope'),
    ('nmap', ['10.0.0.99'], 'outside the engagement scope'),                 # explicit exclusion
    ('nmap', ['10.0.1.0/24'], 'not entirely inside'),
    ('nmap', ['10.0.0.0/25'], 'not entirely inside'),                      # contains an exclusion
    ('nmap', ['10.0.0.1-50'], 'range syntax'),
    ('nmap', ['-iL', 'targets.txt'], '-iL'),
    ('nmap', ['-iR', '100'], '-iR'),
    ('nmap', ['--script=dos', '10.0.0.5'], 'dos/exploit'),
    ('nmap', ['--script', '/tmp/evil.nse', '10.0.0.5'], 'custom script'),
    ('curl', ['https://evil.test/x'], 'evil.test is outside'),
    ('curl', ['https://admin.example.com/'], 'outside the engagement scope'),
    ('curl', ['--proxy', 'http://203.0.113.5:8080', 'https://example.com'], '203.0.113.5'),
    ('curl', ['-o', '/home/op/.ssh/authorized_keys', 'https://example.com/k'], 'credential stores'),
    ('curl', ['file:///etc/passwd'], 'file://'),
    ('curl', ['http://127.0.0.1:8765/api/session'], 'workbench listener'),
    ('curl', ['localhost:8765'], 'workbench listener'),
    ('dig', ['@1.1.1.1', 'example.com'], '1.1.1.1 is outside'),
    ('sqlmap', ['-u', 'http://example.com/?id=1', '--os-shell'], '--os-shell'),
    ('sqlmap', ['-u', 'http://example.com/?id=1', '--eval=import os'], '--eval'),
    ('wget', ['-e', 'robots=off', 'http://example.com'], '-e'),
    ('bash', ['-c', 'id'], 'deny list'),
    ('python3.12', ['-c', 'print(1)'], 'deny list'),
    ('rm', ['-rf', '/'], 'deny list'),
    ('mkfs.ext4', ['/dev/sda1'], 'deny list'),
    ('sudo', ['nmap', '10.0.0.5'], 'deny list'),
    ('nc', ['-lvnp', '4444'], 'deny list'),
    ('cat', ['/home/op/.config/foxworx-workbench/settings.env'], 'credential stores'),
    # Scheme-less host/path and alternate IPv4 encodings (resolver tricks).
    ('curl', ['evil.test/path'], 'evil.test is outside'),
    ('curl', ['2130706433:8765/api/session'], 'non-canonical'),
    ('curl', ['http://2130706433:8765/'], 'outside the engagement scope'),
    ('curl', ['0x7f000001:8765'], 'non-canonical'),
    ('curl', ['127.1:8765'], 'non-canonical'),
    ('curl', ['0177.0.0.1:8765'], 'non-canonical'),
    ('curl', ['134744072'], 'non-canonical'),
    ('curl', ['http://0.0.0.0:8765/'], 'workbench listener'),
    ('curl', ['http://[::ffff:127.0.0.1]:8765/'], 'workbench listener'),
])
def test_dangerous_or_out_of_scope_commands_blocked(tool, args, reason):
    problems = check_command(tool, args, INC, EXC, workbench_port=8765)
    assert problems and any(reason in p for p in problems), problems


def test_protected_paths_are_blocked():
    assert check_command('cat', ['/srv/fox/state/workbench-v2.sqlite3'], INC, EXC,
                         protected_paths=('/srv/fox/state',))


@pytest.mark.asyncio
async def test_out_of_scope_autonomous_command_is_blocked_logged_and_never_runs(setup_cli, store):
    runner, jobs, gateway, _, spec = setup_cli
    gateway.replies = [action('run_tool', name='fixture-good', args=['https://evil.test/'], reason='Try outside'),
                       action('run_tool', name='fixture-good', args=['http://127.0.0.1/'], reason='Stay in scope'),
                       finish()]
    started = runner.start(OWNER, RabbitSpec(**spec))
    result = await settle(runner, started['id'])
    blocked, ran = result['steps'][0], result['steps'][1]
    assert blocked['status'] == 'blocked' and 'evil.test is outside' in blocked['detail']
    assert 'job_id' not in blocked
    assert ran['status'] == 'succeeded'
    events = store.events(OWNER)
    block_events = [e for e in events if e['action'] == 'rabbit-command-blocked']
    assert block_events and 'evil.test' in block_events[0]['details']
    job_events = [e for e in events if e['action'] == 'rabbit-auto-job']
    assert len(job_events) == 1 and 'http://127.0.0.1/' in job_events[0]['details']
    # Only the in-scope command ever produced a job.
    assert [j['argv'][1:] for j in store.list('job', OWNER)] == [['http://127.0.0.1/']]
    # The model is told why so it can replan.
    assert 'Guardrail blocked' in str(gateway.requests[1][0])


@pytest.mark.asyncio
async def test_denied_executable_is_blocked_before_launch(setup_cli, store):
    runner, jobs, gateway, _, spec = setup_cli
    gateway.replies = [action('run_tool', name='rm', args=['--version'], reason='Should never run'), finish()]
    started = runner.start(OWNER, RabbitSpec(**spec))
    result = await settle(runner, started['id'])
    assert result['steps'][0]['status'] == 'blocked' and 'deny list' in result['steps'][0]['detail']
    assert store.list('job', OWNER) == []


def test_job_layer_enforces_guard_even_if_caller_skips_it(setup_cli, store, engagement):
    """Defence in depth: delegation itself rejects an out-of-scope argv."""
    runner, jobs, _, _, spec = setup_cli
    record = store.put('rabbit', OWNER, {'engagement_id': engagement['id'], 'target': '127.0.0.1', 'status': 'running',
                                         'execution_mode': 'installed_cli', 'profiles': [], 'policy_revision': 'trusted',
                                         'instruction_revision': 0, 'budget_end': 9e12, 'decisions_used': 1,
                                         'max_steps': 6, 'max_minutes': 1,
                                         'authorization_stamp': __import__('foxworx.jobs', fromlist=['scope_stamp']).scope_stamp(engagement)})
    proposal = jobs.prepare(OWNER, ProposalSpec(engagement_id=engagement['id'], tool='fixture-good',
                                                args=['203.0.113.9'], target='127.0.0.1'), source='rabbit-hole')
    with pytest.raises(HTTPException) as caught:
        jobs.delegate(OWNER, proposal, record['id'], 0)
    assert caught.value.status_code == 403 and 'outside the engagement scope' in caught.value.detail


def test_human_reviewed_proposals_show_guardrail_warnings(setup_cli, engagement):
    _, jobs, _, _, _ = setup_cli
    proposal = jobs.prepare(OWNER, ProposalSpec(engagement_id=engagement['id'], tool='fixture-good',
                                                args=['198.51.100.4'], target=''))
    assert proposal['guardrail_findings'] == ['198.51.100.4 is outside the engagement scope']
    assert any(w.startswith('Guardrail: ') for w in proposal['warnings'])
