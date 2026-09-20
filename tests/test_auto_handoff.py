"""Fan authority regression tests using real YAML predicates/actions.

The small action walker checks command routing, not HA scheduling or RF delivery.
"""
import itertools
import unittest

from jinja2.nativetypes import NativeEnvironment
import test_solar_control as solar

CORE = solar.CORE
CONTROLLER = next(a for a in CORE['automation'] if a['id'] == 'crispy_controller_v2')


class AutoHandoff(unittest.TestCase):
    render = solar.SolarControl.render
    check = solar.SolarControl.check

    def setUp(self):
        solar.SolarControl.setUp(self)
        self.states.update({
            'input_boolean.crispy_quiet_mode': 'off',
            'input_boolean.crispy_external_override': 'off',
            'input_select.crispy_external_latched_mode': 'none',
            'input_select.crispy_held_fan_demand': 'high',
            'sensor.crispy_raw_fan_demand': 'low',
            'sensor.crispy_requested_fan_mode': 'none',
            'sensor.crispy_hrc_remaining_time': '45',
            'input_boolean.crispy_fan_override_active': 'on',
            'input_select.crispy_last_auto_mode': 'high',
            'input_boolean.crispy_bypass_forced': 'on',
            'binary_sensor.crispy_bypass_requested': 'on',
        })
        self.native = NativeEnvironment()
        self.native.globals.update(self.env.globals)
        self.calls = []

    def test_low_or_none_releases_even_if_high_is_still_held(self):
        for raw, held in itertools.product(['low', 'none'], ['low', 'medium', 'high', 'none']):
            self.states['sensor.crispy_raw_fan_demand'] = raw
            self.states['input_select.crispy_held_fan_demand'] = held
            self.assertEqual(self.render('crispy_requested_fan_mode'), 'none')

    def test_no_automatic_requested_low_in_any_combination(self):
        for raw, held, quiet in itertools.product(
            ['none', 'low', 'medium', 'high', 'unknown'],
            ['none', 'low', 'medium', 'high', 'unknown'], ['on', 'off']
        ):
            self.states.update({
                'sensor.crispy_raw_fan_demand': raw,
                'input_select.crispy_held_fan_demand': held,
                'input_boolean.crispy_quiet_mode': quiet,
            })
            result = self.render('crispy_requested_fan_mode')
            self.assertIn(result, ['none', 'medium', 'high'])
            if quiet == 'on':
                self.assertEqual(result, 'none')

    def test_each_low_demand_source_releases(self):
        keys = ['sensor.crispy_applied_thermal_demand', 'sensor.crispy_moisture_demand', 'sensor.crispy_laundry_demand']
        for source in keys:
            self.states.update(dict.fromkeys(keys, 'none'))
            self.states[source] = 'low'
            self.states['sensor.crispy_raw_fan_demand'] = self.render('crispy_raw_fan_demand')
            self.assertEqual(self.render('crispy_requested_fan_mode'), 'none')

    def test_justified_medium_and_high_boosts_remain(self):
        for level in ['medium', 'high']:
            self.states['sensor.crispy_raw_fan_demand'] = level
            self.states['input_select.crispy_held_fan_demand'] = level
            self.assertEqual(self.render('crispy_requested_fan_mode'), level)

    def test_cruise_releases_unless_urgent(self):
        self.states['input_select.crispy_thermal_phase'] = 'cruise'
        for aggression, expected in [('normal', 'none'), ('urgent', 'medium')]:
            self.states['sensor.crispy_predictive_aggression'] = aggression
            demand = self.render('crispy_cruise_demand')
            self.states['sensor.crispy_cruise_demand'] = demand
            applied = self.render('crispy_applied_thermal_demand')
            self.states['sensor.crispy_raw_fan_demand'] = applied
            self.states['input_select.crispy_held_fan_demand'] = applied
            self.assertEqual(self.render('crispy_requested_fan_mode'), expected)

    def condition(self, condition, variables):
        if condition['condition'] == 'template':
            return bool(self.native.from_string(condition['value_template']).render(**variables))
        if condition['condition'] == 'state':
            return self.states.get(condition['entity_id']) == condition['state']
        self.fail(f'Unhandled condition: {condition}')

    def walk(self, actions, variables):
        for action in actions:
            if 'choose' in action:
                selected = next((b for b in action['choose'] if all(self.condition(c, variables) for c in b['conditions'])), None)
                self.walk(selected['sequence'] if selected else action.get('default', []), variables)
            elif 'if' in action:
                self.walk(action.get('then', []) if all(self.condition(c, variables) for c in action['if']) else action.get('else', []), variables)
            elif 'action' in action:
                service = action['action']
                if service == 'script.crispy_apply_bypass_policy':
                    self.walk(CORE['script']['crispy_apply_bypass_policy']['sequence'], variables)
                elif service.startswith('script.crispy_send_'):
                    self.calls.append(service)
                elif service == 'input_select.select_option':
                    self.states[action['target']['entity_id']] = self.native.from_string(action['data']['option']).render(**variables)
                else:
                    self.fail(f'Unhandled action: {service}')
            elif 'wait_template' not in action:
                self.fail(f'Unhandled action: {action}')

    def run_release(self):
        self.states['sensor.crispy_expected_mode'] = self.render('crispy_expected_mode')
        variables = {}
        for key, value in CONTROLLER['actions'][1]['variables'].items():
            variables[key] = self.native.from_string(value).render(**variables)
        self.walk([CONTROLLER['actions'][-1]], variables)

    def test_owned_boost_releases_while_bypass_stays_open(self):
        self.run_release()
        self.assertEqual(self.calls, ['script.crispy_send_fan_auto'])

    def test_auto_cooling_can_open_bypass_without_fan_boost(self):
        self.states['binary_sensor.crispy_hrc_bypass_open'] = 'off'
        self.run_release()
        self.assertEqual(self.calls, ['script.crispy_send_fan_auto', 'script.crispy_send_bypass_open'])

    def test_ended_thermal_demand_returns_bypass_too(self):
        self.states['binary_sensor.crispy_bypass_requested'] = 'off'
        self.run_release()
        self.assertEqual(self.calls, ['script.crispy_send_fan_auto', 'script.crispy_send_bypass_auto'])

    def test_native_high_does_not_block_release_of_owned_boost(self):
        self.states['input_boolean.crispy_external_override'] = 'on'
        self.states['input_select.crispy_external_latched_mode'] = 'high'
        self.run_release()
        self.assertEqual(self.states['sensor.crispy_expected_mode'], 'hold')
        self.assertEqual(self.calls, ['script.crispy_send_fan_auto'])

    def test_no_owned_boost_means_no_repeated_auto_command(self):
        self.states['input_boolean.crispy_fan_override_active'] = 'off'
        self.states['input_select.crispy_last_auto_mode'] = 'hold'
        self.states['input_boolean.crispy_external_override'] = 'on'
        self.run_release()
        self.assertEqual(self.calls, [])

    def test_old_low_override_is_released_after_upgrade(self):
        self.states['input_boolean.crispy_fan_override_active'] = 'off'
        self.states['input_select.crispy_last_auto_mode'] = 'low'
        self.run_release()
        self.assertIn('script.crispy_send_fan_auto', self.calls)

    def test_native_low_latch_cannot_block_later_medium_boost(self):
        self.states.update({
            'input_boolean.crispy_external_override': 'on',
            'input_select.crispy_external_latched_mode': 'low',
            'sensor.crispy_hrc_fan_mode': 'low',
            'sensor.crispy_requested_fan_mode': 'medium',
        })
        predicate = CONTROLLER['actions'][1]['variables']['external_higher']
        self.assertFalse(self.native.from_string(predicate).render())
        self.assertEqual(self.render('crispy_expected_mode'), 'medium')

    def test_sync_allows_native_high_but_requires_release_and_bypass(self):
        self.states['sensor.crispy_expected_mode'] = 'hold'
        self.check('controller_synced', False)
        self.states['input_boolean.crispy_fan_override_active'] = 'off'
        self.check('controller_synced', True)
        self.states['binary_sensor.crispy_hrc_bypass_open'] = 'off'
        self.check('controller_synced', False)

    def test_automatic_command_graph_has_no_low_path(self):
        def visit(value, visited):
            if isinstance(value, dict):
                service = value.get('action', '')
                self.assertNotEqual(service, 'script.crispy_send_low_60')
                self.assertNotEqual(value.get('command'), 'low_60')
                if service.startswith('script.') and service not in visited:
                    visited.add(service)
                    visit(CORE['script'][service.split('.', 1)[1]], visited)
                for child in value.values():
                    visit(child, visited)
            elif isinstance(value, list):
                for child in value:
                    visit(child, visited)
        visit(CORE['automation'], set())


if __name__ == '__main__':
    unittest.main()
