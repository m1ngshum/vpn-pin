import contextlib
import copy
import io
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import vpn_pin as vpn
import vpn_pin_pf as pf


class PFBackend:
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        temporary = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.directory = Path(temporary) / '501'
        self.directory.mkdir()
        self.stack.enter_context(patch.object(pf, 'private_file', side_effect=lambda p: p.stat()))
        self.stack.enter_context(patch.object(pf, 'source_address', return_value='192.0.2.10'))
        self.root = 'anchor "main/*" all\nblock drop all\n'
        self.anchors = {}
        self.states = ''
        self.events = []
        self.fail_load = None
        self.fail_kill = False
        self.fail_flush = False
        self.state = {'pid': 123, 'started': 'fixture start', 'binary': '/fixture/openvpn',
                      'utun': 'utun42', 'ifindex': 42, 'log_guard': {'size': 1},
                      'routes': ['198.51.100.1', '198.51.100.2']}
        self.healthy = True
        self.stack.enter_context(patch.object(pf, 'pf', side_effect=self.backend))

    def verify(self):
        if not self.healthy:
            raise vpn.Error('original interface/process lifetime lost')

    def backend(self, *args, text=None):
        if args == ('-s', 'rules'):
            return self.root
        if args == ('-s', 'info'):
            return 'Status: Enabled for 1 days'
        if args == ('-a', 'main', '-s', 'Anchors'):
            return '\n'.join(self.anchors)
        if '-n' in args:
            return text
        if '-f' in args:
            anchor = args[args.index('-a') + 1]
            self.events.append('load')
            self.anchors[anchor] = pf.normalize(text)
            self.states = 'fixture active states'
            if self.fail_load:
                raise self.fail_load
            return ''
        if args[:1] == ('-a',):
            anchor = args[1]
            if args[-2:] == ('-s', 'rules'):
                return self.anchors.get(anchor, '')
            if args[-2:] == ('-F', 'rules'):
                self.events.append('flush')
                if self.fail_flush:
                    raise pf.Error('flush failed')
                self.anchors[anchor] = ''
                return ''
        if '-k' in args:
            self.events.append(('kill', args[1], args[3], args[5]))
            if self.fail_kill:
                raise pf.Error('kill failed')
            self.states = ''
            return ''
        if args[-2:] == ('-s', 'states'):
            return self.states
        raise AssertionError(args)

    def enable(self):
        def reserve(record):
            self.state['pf_token']=record['token']
            (self.directory/'work.json').write_text(json.dumps(self.state))
        return pf.enable(self.directory, 'work', self.state, self.verify, reserve)


class Permissions(PFBackend, unittest.TestCase):
    def test_narrow_permissions_durable_intent_and_cleanup_order(self):
        original_store = pf.store
        phases = []
        def store(*args):
            phases.append(args[2]['phase'])
            if args[2]['phase'] == 'prepared':
                self.assertNotIn('load', self.events)
            original_store(*args)
        with patch.object(pf, 'store', side_effect=store):
            record = self.enable()
            self.assertEqual(pf.load(self.directory, 'work')['phase'], 'active')
            for line in record['expected'].splitlines():
                self.assertIn('pass out quick on utun42 inet proto tcp from 192.0.2.10', line)
                self.assertIn('port = 443', line)
                self.assertIn('keep state (if-bound)', line)
            pf.revoke(self.directory, 'work', record, self.verify)
        self.assertEqual(phases, ['prepared', 'active', 'rules-removed', 'closed'])
        self.assertLess(self.events.index('flush'), next(i for i, x in enumerate(self.events) if isinstance(x, tuple)))
        self.assertEqual([x for x in self.events if isinstance(x, tuple)],
                         [('kill', 'utun42', '192.0.2.10', ip) for ip in self.state['routes']])
        self.assertEqual(pf.load(self.directory, 'work')['phase'], 'closed')
        self.healthy = False  # Closed receipt must not inspect/adopt a later interface.
        pf.revoke(self.directory, 'work', record, self.verify)

    def test_uncertain_or_interrupted_load_rolls_back_from_saved_intent(self):
        for failure in (pf.Error('load timed out'), KeyboardInterrupt()):
            with self.subTest(failure=failure):
                self.events.clear();self.states='';self.fail_load=failure
                with self.assertRaises(type(failure)):
                    self.enable()
                self.assertEqual(pf.load(self.directory, 'work')['phase'], 'closed')
                self.assertTrue(all(not x for x in self.anchors.values()))

    def test_failed_intent_save_never_loads_rules(self):
        with patch.object(pf, 'store', side_effect=OSError('disk failed')):
            with self.assertRaises(OSError):
                self.enable()
        self.assertNotIn('load', self.events)

    def test_failed_active_save_still_revokes_permissions(self):
        original = pf.store
        def store(d, n, record):
            if record['phase'] == 'active':
                raise OSError('active save failed')
            original(d, n, record)
        with patch.object(pf, 'store', side_effect=store), self.assertRaises(OSError):
            self.enable()
        self.assertEqual(pf.load(self.directory, 'work')['phase'], 'closed')
        self.assertTrue(all(not x for x in self.anchors.values()))

    def test_foreign_anchor_rule_is_never_flushed(self):
        record = self.enable()
        self.anchors[record['anchor']] += '\nblock drop all'
        with self.assertRaises(pf.Error):
            pf.revoke(self.directory, 'work', record, self.verify)
        self.assertNotIn('flush', self.events)
        self.assertFalse(any(isinstance(x, tuple) for x in self.events))

    def test_owner_loss_revokes_rules_but_never_uses_replacement_interface(self):
        record = self.enable();self.healthy=False
        with self.assertRaises(vpn.Error):
            pf.revoke(self.directory, 'work', record, self.verify)
        self.assertEqual(self.anchors[record['anchor']], '')
        self.assertFalse(any(isinstance(x, tuple) for x in self.events))
        self.assertEqual(pf.load(self.directory, 'work')['phase'], 'rules-removed')

    def test_partial_state_cleanup_and_failed_close_save_retain_journal(self):
        record=self.enable();self.fail_kill=True
        with self.assertRaises(pf.Error):
            pf.revoke(self.directory, 'work', record, self.verify)
        self.assertEqual(pf.load(self.directory, 'work')['phase'], 'rules-removed')
        self.fail_kill=False
        original=pf.store
        def store(d,n,r):
            if r['phase']=='closed':raise OSError('close save failed')
            original(d,n,r)
        with patch.object(pf,'store',side_effect=store),self.assertRaises(OSError):
            pf.revoke(self.directory,'work',record,self.verify)
        self.assertEqual(pf.load(self.directory,'work')['phase'],'rules-removed')
        pf.revoke(self.directory,'work',record,self.verify)
        self.assertEqual(pf.load(self.directory,'work')['phase'],'closed')

    def test_closed_receipt_does_not_authorize_foreign_anchor_cleanup(self):
        record=self.enable();pf.revoke(self.directory,'work',record,self.verify)
        self.events.clear();self.anchors[record['anchor']]='block foreign'
        with self.assertRaises(pf.Error):pf.revoke(self.directory,'work',record,self.verify)
        self.assertEqual(self.events,[])

    def test_closed_receipt_reappearing_identical_rules_are_not_cleanup_authority(self):
        record = self.enable()
        pf.revoke(self.directory, 'work', record, self.verify)
        self.events.clear()
        self.anchors[record['anchor']] = record['expected']
        self.states = 'new states'
        with self.assertRaises(pf.Error):
            pf.revoke(self.directory, 'work', record, self.verify)
        with self.assertRaises(pf.Error):
            pf.guard(self.directory, 'work')
        self.assertEqual(self.events, [])
        self.assertEqual(self.states, 'new states')

    def test_missing_journal_runtime_reference_blocks_all_mutation(self):
        record = self.enable()
        pf.path(self.directory, 'work').unlink()
        profile = vpn.Profile('work', Path('/fixture.ovpn'), [], '', '')
        calls = [lambda: vpn.down(self.directory, 'work'),
                 lambda: vpn.up(self.directory, profile),
                 lambda: vpn.clean_stale(self.directory, 'work', self.state),
                 lambda: vpn.pin_routes(self.directory, profile, self.state),
                 lambda: vpn.wrapper(self.directory, profile),
                 lambda: vpn.restart_owned(self.directory, Path('/fixture'), profile, self.state, [], None)]
        self.events.clear()
        with patch.object(vpn, 'read_state') as read, patch.object(vpn.os, 'kill') as kill, patch.object(vpn, 'remove_route') as remove:
            for call in calls:
                with self.assertRaisesRegex(pf.Error, 'missing or replaced'):
                    call()
            read.assert_not_called()
            kill.assert_not_called()
            remove.assert_not_called()
        self.assertEqual(self.events, [])
        self.assertEqual(self.anchors[record['anchor']], record['expected'])

    def test_failed_runtime_reservation_never_grants(self):
        def fail(record):
            raise OSError('runtime save failed')
        with self.assertRaises(OSError):
            pf.enable(self.directory, 'work', self.state, self.verify, fail)
        self.assertNotIn('load', self.events)
        self.assertEqual(pf.load(self.directory, 'work')['phase'], 'closed')

    def test_superseded_token_cannot_mutate_replacement(self):
        old=self.enable();pf.revoke(self.directory,'work',old,self.verify)
        new=self.enable();self.events.clear()
        with self.assertRaises(pf.Error):pf.revoke(self.directory,'work',old,self.verify)
        self.assertEqual(self.events,[])
        self.assertEqual(self.anchors[new['anchor']],new['expected'])

    def test_guard_cannot_be_bypassed_by_ordinary_mutating_commands(self):
        self.enable()
        calls=[lambda:vpn.up(self.directory,vpn.Profile('work',Path('/fixture.ovpn'),[], '', '')),
               lambda:vpn.clean_stale(self.directory,'work',self.state),
               lambda:vpn.wrapper(self.directory,vpn.Profile('work',Path('/fixture.ovpn'),[], '', '')),
               lambda:vpn.pin_routes(self.directory,vpn.Profile('work',Path('/fixture.ovpn'),[], '', ''),self.state),
               lambda:vpn.restart_owned(self.directory,Path('/fixture'),vpn.Profile('work',Path('/fixture.ovpn'),[], '', ''),self.state,[],None)]
        with patch.object(vpn,'run') as run,patch.object(vpn,'read_state') as read:
            for call in calls:
                with self.assertRaises(pf.Error):call()
            run.assert_not_called();read.assert_not_called()

    def test_down_does_not_remove_routes_or_signal_until_pf_cleanup_succeeds(self):
        record=self.enable();self.fail_flush=True
        with patch.object(vpn,'read_state',return_value=self.state),patch.object(vpn.os,'kill') as kill,patch.object(vpn,'remove_route') as remove:
            with self.assertRaises(pf.Error):vpn.down(self.directory,'work')
            kill.assert_not_called();remove.assert_not_called()
        self.assertEqual(pf.load(self.directory,'work')['token'],record['token'])

    def test_absent_runtime_still_revokes_owned_anchor_then_retains_pending_journal(self):
        record=self.enable()
        with patch.object(vpn,'read_state',return_value=None),patch.object(vpn.os,'kill') as kill:
            with self.assertRaises(vpn.Error):vpn.down(self.directory,'work')
            kill.assert_not_called()
        self.assertEqual(self.anchors[record['anchor']],'')
        self.assertEqual(pf.load(self.directory,'work')['phase'],'rules-removed')

    def test_preexisting_states_no_hook_and_normalization_changes_block_apply(self):
        self.states='foreign states'
        with self.assertRaises(pf.Error):self.enable()
        self.states='';self.root='block drop all'
        with self.assertRaises(pf.Error):self.enable()
        self.assertNotIn('load',self.events)

    def test_malformed_runtime_reference_is_not_unmanaged(self):
        for data in ([], {'pf_token': None}, {'pf_token': 'invalid'}):
            (self.directory / 'work.json').write_text(json.dumps(data))
            with self.assertRaises(pf.Error):
                pf.guard(self.directory, 'work')

    def test_malformed_pending_journal_is_not_missing(self):
        pf.path(self.directory,'work').write_text('{broken')
        with self.assertRaises(pf.Error):pf.guard(self.directory,'work')

class Supervision(PFBackend, unittest.TestCase):
    # Share the mocked PF backend, but run the real foreground coordinator.
    def run_supervisor(self, tick, real_signals=False):
        profile=vpn.Profile('work',Path('/fixture/client.ovpn'),self.state['routes'],'','')
        @contextlib.contextmanager
        def directory(*args):yield self.directory
        patches=[patch.object(vpn,'load_profiles',return_value={'work':profile}),
                 patch.object(vpn,'client_identity',return_value=('fixture',)),
                 patch.object(vpn,'resolve_ips',return_value=self.state['routes']),
                 patch.object(vpn,'state_directory',side_effect=directory),
                 patch.object(vpn,'read_state',side_effect=lambda *args:self.runtime),
                 patch.object(vpn,'save_state',side_effect=lambda d,n,state:(self.directory/'work.json').write_text(json.dumps(state))),
                 patch.object(vpn,'owns_process',return_value=True),
                 patch.object(vpn,'require_lifetime',side_effect=lambda *args:self.life()),
                 patch.object(vpn,'exact_route',return_value='utun42'),
                 patch.object(vpn.time,'sleep',side_effect=tick),
                 contextlib.redirect_stdout(io.StringIO())]
        if not real_signals:
            patches += [patch.object(vpn.signal, 'signal'), patch.object(vpn.signal, 'pthread_sigmask', return_value=set())]
        self.runtime=self.state;self.life=lambda:None
        with contextlib.ExitStack() as stack:
            for item in patches:stack.enter_context(item)
            return vpn.supervise_api_pf(Path('/fixture/profiles.conf'),'work')

    def test_foreground_interrupt_revokes_and_keeps_tunnel(self):
        def stop(*args):raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):self.run_supervisor(stop)
        self.assertEqual(pf.load(self.directory,'work')['phase'],'closed')
        self.assertEqual(self.runtime,self.state)

    def test_pending_real_stop_at_enable_return_is_revoked_after_assignment(self):
        original = pf.enable
        def enable(*args):
            record = original(*args)
            os.kill(os.getpid(), signal.SIGTERM)  # Queued while the coordinator blocks stop signals.
            return record
        with patch.object(pf, 'enable', side_effect=enable), self.assertRaises(KeyboardInterrupt):
            self.run_supervisor(lambda *args: self.fail('pending signal was not delivered'), real_signals=True)
        self.assertEqual(pf.load(self.directory, 'work')['phase'], 'closed')
        self.assertTrue(all(not x for x in self.anchors.values()))
        self.assertEqual(self.states, '')

    def test_missing_journal_supervisor_recovers_only_its_record_and_revokes(self):
        def missing(*args):
            pf.path(self.directory, 'work').unlink()
        with self.assertRaises(pf.Error):
            self.run_supervisor(missing)
        self.assertEqual(pf.load(self.directory, 'work')['phase'], 'closed')
        self.assertTrue(all(not x for x in self.anchors.values()))

    def test_managed_down_closed_receipt_ends_supervisor_without_runtime_adoption(self):
        def stopped(*args):
            r=pf.load(self.directory,'work');pf.revoke(self.directory,'work',r,self.verify)
            self.runtime=None
        self.run_supervisor(stopped)
        self.assertEqual(pf.load(self.directory,'work')['phase'],'closed')

    def test_lifetime_loss_revokes_anchor_and_retains_unresolved_states(self):
        def lost(*args):
            def invalid():raise vpn.ContinuityError('interface recycled')
            self.life=invalid
        with self.assertRaises(vpn.ContinuityError):self.run_supervisor(lost)
        self.assertTrue(all(not x for x in self.anchors.values()))
        self.assertEqual(pf.load(self.directory,'work')['phase'],'rules-removed')
        self.assertFalse(any(isinstance(x,tuple) for x in self.events))

    def test_old_supervisor_never_revokes_replacement_permission(self):
        replacement=[]
        def replace(*args):
            old=pf.load(self.directory,'work');pf.revoke(self.directory,'work',old,self.verify)
            replacement.append(self.enable())
        with self.assertRaises(pf.Error):self.run_supervisor(replace)
        self.assertEqual(self.anchors[replacement[0]['anchor']],replacement[0]['expected'])
        self.assertEqual(pf.load(self.directory,'work')['phase'],'active')

class CliAndOwnership(unittest.TestCase):
    def test_cli_dispatches_explicit_foreground_command(self):
        profile=vpn.Profile('work',Path('/fixture.ovpn'),['192.0.2.1'],'','')
        with patch.object(vpn.sys,'platform','darwin'),patch.object(vpn.os,'geteuid',return_value=0),patch.object(vpn.os,'umask'),patch.object(vpn,'load_profiles',return_value={'work':profile}),patch.object(vpn,'supervise_api_pf') as run:
            vpn.main(['--config','/fixture/profiles.conf','api-pf','work'])
        run.assert_called_once_with(Path('/fixture/profiles.conf'),'work')

    def test_source_route_process_and_frozen_state_are_all_required(self):
        state={'pid':123,'utun':'utun42','ifindex':42,'routes':['192.0.2.1']}
        for current,owned,route,source in [(None,True,'utun42','192.0.2.10'),(dict(state,pid=999),True,'utun42','192.0.2.10'),(state,False,'utun42','192.0.2.10'),(state,True,'utun9','192.0.2.10'),(state,True,'utun42','192.0.2.11')]:
            with self.subTest(current=current,owned=owned,route=route,source=source),patch.object(vpn,'read_state',return_value=current),patch.object(vpn,'owns_process',return_value=owned),patch.object(vpn,'require_lifetime'),patch.object(vpn,'exact_route',return_value=route),patch.object(pf,'source_address',return_value=source),self.assertRaises(vpn.Error):
                vpn.require_pf_session(Path('/fixture'),'work',state,'192.0.2.10')


if __name__=='__main__':unittest.main()
