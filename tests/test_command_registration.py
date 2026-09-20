"""Validate registration packets and missing-release preflight, without RF."""
import unittest
from types import SimpleNamespace
import yaml
from jinja2.nativetypes import NativeEnvironment
from test_solar_control import CORE, ROOT, ENTITIES


class Commands(unittest.TestCase):
    def test_registration_packet_lengths_and_existing_payloads(self):
        automation = yaml.safe_load((ROOT / 'examples/register_crispy_commands.yaml').read_text())
        variables = automation['variables']
        commands = variables['commands']
        self.assertEqual(len(commands), 21)
        self.assertEqual(len({c['name'] for c in commands}), 21)
        template = automation['actions'][-1]['repeat']['sequence'][0]['data']['packet_string']
        env = NativeEnvironment()
        packets = {}
        for command in commands:
            packet = env.from_string(template).render(**variables, repeat=SimpleNamespace(item=command))
            parts = packet.split()
            self.assertEqual(parts[2:5], ['37:099999', '32:142350', '--:------'])
            self.assertEqual(int(parts[6]), len(bytes.fromhex(parts[7])))
            packets[command['name']] = ' '.join(parts)
        self.assertEqual(packets['auto'], 'I --- 37:099999 32:142350 --:------ 22F1 003 000407')
        self.assertTrue(packets['high_60'].endswith('22F3 007 00123C03040404'))
        self.assertTrue(packets['bypass_auto'].endswith('22F7 003 00FFEF'))
        self.assertEqual(commands[0]['name'], 'auto')
        self.assertEqual(automation['actions'][-1]['repeat']['sequence'][0]['action'], 'ramses_cc.add_command')

    def test_missing_auto_blocks_boosts_but_release_preserves_ownership(self):
        env = NativeEnvironment()
        for commands, modes, available in [
            ({'medium_60': 'packet', 'bypass_auto': 'packet'}, [], False),
            (None, None, False),
            ({'auto': ''}, [], False),
            ({'auto': 'I --- packet'}, [], True),
            ({'auto': {'verb': 'I', 'code': '22F1', 'payload': '000407'}}, [], True),
            ({}, ['auto', 'high'], True),
        ]:
            attrs = {'commands': commands, 'strategy_modes': modes}
            env.globals.update(states=lambda _: 'remote.rem_37_099999', state_attr=lambda _, key: attrs.get(key))
            self.assertEqual(str(env.from_string(ENTITIES['crispy_fan_auto_available']['state']).render()).strip(), str(available))
            for script in ['crispy_send_low_60', 'crispy_send_medium_60', 'crispy_send_high_60', 'crispy_send_fan_auto']:
                guard = CORE['script'][script]['sequence'][0]
                self.assertEqual(str(env.from_string(guard['if'][0]['value_template']).render()).strip(), str(not available))
                # The guard precedes RF/log/ownership writes. Missing AUTO never clears ownership.
                self.assertEqual(guard['then'][0]['action'], 'persistent_notification.create')
                self.assertIn('stop', guard['then'][-1])
                self.assertEqual(guard['then'][-1]['error'], script != 'crispy_send_fan_auto')
