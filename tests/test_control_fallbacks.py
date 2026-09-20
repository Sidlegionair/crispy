"""Regression tests for bypass failure, forecast expiry and snapshot capture."""
from datetime import datetime, timedelta, timezone
import math
import unittest

import yaml
import test_solar_control as solar
from test_solar_control import CORE, ROOT

WEATHER = yaml.safe_load((ROOT / 'packages/crispy_weather.yaml').read_text())


class ControlFallbacks(unittest.TestCase):
    render = solar.SolarControl.render
    check = solar.SolarControl.check

    def setUp(self):
        solar.SolarControl.setUp(self)
        self.now = datetime(2026, 9, 20, 12, 30, tzinfo=timezone.utc)
        self.attrs = {}

        def timestamp(value, default=None):
            try:
                if isinstance(value, datetime):
                    return value.timestamp()
                return datetime.fromisoformat(value).timestamp()
            except (ValueError, TypeError):
                return default

        def numeric(value):
            try:
                return math.isfinite(float(value))
            except (ValueError, TypeError):
                return False

        self.env.globals.update(
            now=lambda: self.now,
            as_timestamp=timestamp,
            is_number=numeric,
            state_attr=lambda entity, attr: self.attrs.get((entity, attr)),
        )
        self.states.update({
            'binary_sensor.crispy_bypass_requested': 'on',
            'sensor.crispy_applied_thermal_demand': 'high',
            'sensor.crispy_weather_hourly': self.now.isoformat(),
            'binary_sensor.crispy_forecast_fresh': 'on',
            'sensor.crispy_forecast_heat_risk': 'extreme',
            'sensor.crispy_forecast_current_window_hours': '3',
            'sensor.crispy_predictive_target_offset': '1',
            'input_number.crispy_target': '20',
            'input_boolean.crispy_heatwave_mode': 'off',
        })
        self.attrs[('sensor.crispy_weather_hourly', 'forecast')] = [
            {'datetime': (self.now + timedelta(minutes=30)).isoformat(), 'temperature': 18}
        ]

    def test_closed_bypass_warm_supply_fails(self):
        self.states['binary_sensor.crispy_hrc_bypass_open'] = 'off'
        self.states['sensor.crispy_hrc_supply_temperature'] = '24.5'
        self.check('bypass_open_failed', True)

    def test_unknown_bypass_warm_supply_fails(self):
        self.states['binary_sensor.crispy_hrc_bypass_open'] = 'unavailable'
        self.states['sensor.crispy_hrc_supply_temperature'] = '24'
        self.check('bypass_open_failed', True)

    def test_missing_ack_with_real_cooling_keeps_running(self):
        self.states['binary_sensor.crispy_hrc_bypass_open'] = 'off'
        self.check('bypass_open_failed', False)

    def test_open_bypass_uses_delivery_watchdog_instead(self):
        self.states['sensor.crispy_hrc_supply_temperature'] = '24.5'
        self.check('bypass_open_failed', False)
        self.check('cooling_delivery_failed', True)

    def test_moisture_only_does_not_require_bypass(self):
        self.states['binary_sensor.crispy_hrc_bypass_open'] = 'off'
        self.states['sensor.crispy_hrc_supply_temperature'] = '24.5'
        self.states['sensor.crispy_applied_thermal_demand'] = 'none'
        self.check('bypass_open_failed', False)

    def test_bypass_failure_routes_to_existing_retry(self):
        retry = next(a for a in CORE['automation'] if a['id'] == 'crispy_cooling_watchdog_retry')
        self.assertTrue(any(t.get('entity_id') == 'binary_sensor.crispy_bypass_open_failed' and t['id'] == 'failed' for t in retry['triggers']))
        branch = retry['actions'][0]['choose'][0]
        self.states['binary_sensor.crispy_bypass_open_failed'] = 'on'
        predicate = branch['conditions'][1]['value_template']
        self.assertEqual(self.env.from_string(predicate).render().strip(), 'True')
        self.assertEqual(branch['sequence'][-1]['action'], 'timer.start')
        self.assertEqual(branch['sequence'][-1]['target']['entity_id'], 'timer.crispy_cooling_retry')

    def test_recent_valid_forecast_is_fresh(self):
        self.check('forecast_fresh', True)

    def test_age_boundary_expires_without_new_fetch(self):
        self.now += timedelta(minutes=90)
        # Keep time coverage valid to isolate fetch-age expiry.
        self.attrs[('sensor.crispy_weather_hourly', 'forecast')] = [
            {'datetime': self.now.isoformat(), 'temperature': 18}
        ]
        self.check('forecast_fresh', False)

    def test_recent_fetch_of_old_or_far_future_points_is_not_fresh(self):
        for minutes in (-60, 120):
            self.attrs[('sensor.crispy_weather_hourly', 'forecast')] = [
                {'datetime': (self.now + timedelta(minutes=minutes)).isoformat(), 'temperature': 18}
            ]
            self.check('forecast_fresh', False)

    def test_missing_or_invalid_forecast_cannot_activate_policy(self):
        for forecast in (None, [], [{'datetime': 'invalid', 'temperature': 18}],
                         [{'datetime': self.now.isoformat(), 'temperature': 'unavailable'}], ['bad point']):
            self.attrs[('sensor.crispy_weather_hourly', 'forecast')] = forecast
            self.check('forecast_fresh', False)

    def test_missing_or_future_fetch_timestamp_is_not_fresh(self):
        for stamp in ('unknown', 'unavailable', (self.now + timedelta(minutes=1)).isoformat()):
            self.states['sensor.crispy_weather_hourly'] = stamp
            self.check('forecast_fresh', False)

    def test_stale_cached_values_cannot_lower_target_or_boost(self):
        self.states['binary_sensor.crispy_forecast_fresh'] = 'off'
        self.states['sensor.crispy_predictive_aggression'] = 'urgent'
        self.assertEqual(self.render('crispy_effective_target'), '20.0')
        self.assertEqual(self.render('crispy_predictive_aggression'), 'off')
        self.assertEqual(self.render('crispy_predictive_thermal_demand'), 'none')

    def test_manual_heatwave_survives_stale_forecast(self):
        self.states['binary_sensor.crispy_forecast_fresh'] = 'off'
        self.states['input_boolean.crispy_heatwave_mode'] = 'on'
        self.assertEqual(self.render('crispy_effective_target'), '19.5')

    def test_fresh_forecast_restores_policy(self):
        self.assertEqual(self.render('crispy_effective_target'), '19.0')
        self.assertEqual(self.render('crispy_predictive_aggression'), 'urgent')

    def test_weather_offset_also_gates_stale_data(self):
        offset = next(e for b in WEATHER['template'] for e in b.get('sensor', []) if e['unique_id'] == 'crispy_predictive_target_offset')
        self.states['binary_sensor.crispy_forecast_fresh'] = 'off'
        self.assertEqual(self.env.from_string(offset['state']).render().strip(), '0')

    def test_empty_fetch_does_not_refresh_receipt_timestamp(self):
        predicate = WEATHER['template'][0]['actions'][-1]['value_template']
        for response in ({}, {'weather.test': {}}, {'weather.test': {'forecast': []}}):
            self.assertEqual(self.env.from_string(predicate).render(crispy_hourly=response, weather_entity='weather.test').strip(), 'False')

    def test_snapshot_captures_values_and_handles_missing_entities(self):
        script = CORE['script']['crispy_diagnostic_snapshot']
        snapshot = self.env.from_string(script['sequence'][0]['variables']['snapshot']).render()
        self.assertIn('sensor.crispy_hrc_indoor_temperature: 24', snapshot)
        self.assertIn('input_text.crispy_last_command: unknown', snapshot)
        self.assertIn(self.now.isoformat(), snapshot)
        self.assertGreater(len(snapshot), 255)
        self.states['sensor.crispy_hrc_indoor_temperature'] = '25'
        notification = script['sequence'][1]
        self.assertEqual(notification['action'], 'persistent_notification.create')
        self.assertEqual(self.env.from_string(notification['data']['message']).render(snapshot=snapshot), snapshot)
        self.assertEqual(notification['data']['notification_id'], 'crispy_diagnostic_snapshot')

    def test_snapshot_has_no_rf_actions(self):
        actions = CORE['script']['crispy_diagnostic_snapshot']['sequence']
        self.assertEqual([a['action'] for a in actions if 'action' in a], ['persistent_notification.create'])


if __name__ == '__main__':
    unittest.main()
