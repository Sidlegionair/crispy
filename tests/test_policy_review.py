"""Policy scenarios render production templates; never transmit RF."""
import ast
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
import yaml
from jinja2 import Environment, StrictUndefined
from test_solar_control import CORE, ROOT, ENTITIES
import test_control_fallbacks as fallbacks

WEATHER = yaml.safe_load((ROOT / 'packages/crispy_weather.yaml').read_text())
W = {e['unique_id']: e for b in WEATHER['template'] for e in b.get('sensor', [])}


class PolicyReview(unittest.TestCase):
    def setUp(self):
        fallbacks.ControlFallbacks.setUp(self)
        self.now = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
        self.env.globals.update(
            as_datetime=lambda x: datetime.fromtimestamp(x, timezone.utc) if isinstance(x, (int,float)) else datetime.fromisoformat(x),
            as_local=lambda x: x,
            today_at=lambda x: datetime.combine(self.now.date(), datetime.strptime(x, '%H:%M').time(), timezone.utc),
            timedelta=timedelta,
            this=SimpleNamespace(state='off'),
        )
        self.states.update({
            'sensor.crispy_hrc_indoor_temperature': '22.84',
            'sensor.crispy_hrc_intake_temperature': '18',
            'sensor.crispy_hrc_supply_temperature': '22.8',
            'input_number.crispy_target': '20',
            'sensor.crispy_effective_target': '20',
            'sensor.crispy_forecast_intake_offset': '0',
            'sensor.crispy_moisture_demand': 'none',
            'sensor.crispy_laundry_demand': 'none',
            'input_number.crispy_cruise_entry_intake': '18',
            'sensor.crispy_forecast_heat_risk': 'low',
            'sun.sun': 'above_horizon',
        })

    def render(self, name):
        return self.env.from_string(ENTITIES[name]['state']).render().strip()

    def forecast(self, rows):
        self.attrs[('sensor.crispy_weather_hourly','forecast')] = [
            {'datetime': (self.now + timedelta(hours=h)).isoformat(), 'temperature': t, 'condition': 'sunny'}
            for h,t in rows
        ]
        result = ast.literal_eval(self.env.from_string(W['crispy_forecast_analysis']['attributes']['result']).render().strip())
        self.attrs[('sensor.crispy_forecast_analysis','result')] = result
        return result

    def offset(self):
        return float(self.env.from_string(W['crispy_predictive_target_offset']['state']).render().strip())

    def test_snapshot_low_risk_never_creates_predictive_demand(self):
        self.states.update({'sensor.crispy_forecast_current_window_hours':'18','sensor.crispy_hrc_intake_temperature':'21.36'})
        aggression = self.render('crispy_predictive_aggression')
        self.assertEqual(aggression, 'normal')
        self.states['sensor.crispy_predictive_aggression'] = aggression
        self.assertEqual(self.render('crispy_predictive_thermal_demand'), 'none')
        self.assertEqual(self.render('crispy_base_thermal_demand'), 'medium')

    def test_even_short_window_low_risk_does_not_boost(self):
        self.states['sensor.crispy_forecast_current_window_hours'] = '0.5'
        self.assertEqual(self.render('crispy_predictive_aggression'), 'normal')

    def test_high_risk_requires_real_closure_and_nearby_heat(self):
        self.states['sensor.crispy_forecast_heat_risk'] = 'high'
        for status,onset,window,expected in [('open_ended',2,2,'normal'),('incomplete',2,1,'normal'),('closing',12,1,'normal'),('closing',4,3,'aggressive'),('closing',4,1,'urgent')]:
            self.states.update({'sensor.crispy_forecast_window_status':status,'sensor.crispy_forecast_heat_onset_hours':str(onset),'sensor.crispy_forecast_current_window_hours':str(window)})
            self.assertEqual(self.render('crispy_predictive_aggression'),expected)

    def test_target_hysteresis_sequence(self):
        for temp,expected in [(20.01,False),(20.29,False),(20.31,True),(20.1,True),(20,False),(20.01,False)]:
            self.states['sensor.crispy_hrc_indoor_temperature']=str(temp)
            actual=self.render('crispy_thermal_call')=='True'
            self.assertEqual(actual,expected)
            self.env.globals['this']=SimpleNamespace(state='on' if actual else 'off')

    def test_all_thermal_boost_sources_obey_near_target_cap(self):
        self.states.update({'sensor.crispy_hrc_indoor_temperature':'20.01','binary_sensor.crispy_thermal_call':'off','sensor.crispy_base_thermal_demand':'low','sensor.crispy_predictive_thermal_demand':'high','sensor.crispy_thermal_momentum_demand':'high','sensor.crispy_recent_heat_gain_demand':'high'})
        self.assertEqual(self.render('crispy_thermal_demand'),'low')
        self.states.update({'sensor.crispy_hrc_indoor_temperature':'20.31','binary_sensor.crispy_thermal_call':'on'})
        self.assertEqual(self.render('crispy_thermal_demand'),'medium')
        self.states['input_select.crispy_control_strategy']='Full Send'
        self.assertEqual(self.render('crispy_thermal_demand'),'high')

    def test_low_power_can_qualify_for_auto_trial(self):
        for power in ('4','45','56'):
            self.states['sensor.crispy_cooling_power']=power
            self.assertEqual(self.render('crispy_cruise_ready'),'True')

    def test_other_boosts_and_cooldown_prevent_trial(self):
        for entity,value in [('sensor.crispy_moisture_demand','medium'),('sensor.crispy_laundry_demand','high'),('input_boolean.crispy_external_override','on'),('timer.crispy_cruise_retry','active')]:
            prior=self.states[entity]
            self.states[entity]=value
            self.assertEqual(self.render('crispy_cruise_ready'),'False')
            self.states[entity]=prior

    def test_trial_requires_release_then_full_settling(self):
        self.states['input_select.crispy_thermal_phase']='cruise'
        self.assertEqual(self.render('crispy_cruise_settled'),'False')
        self.states['input_boolean.crispy_fan_override_active']='off'
        self.assertEqual(self.render('crispy_cruise_settled'),'True')
        self.assertEqual(ENTITIES['crispy_cruise_settled']['delay_on'],'00:15:00')
        self.states.update({'binary_sensor.crispy_cruise_settled':'off','sensor.crispy_indoor_fast_rate':'0.4'})
        self.assertEqual(self.render('crispy_cruise_failing'),'False')
        self.states['binary_sensor.crispy_cruise_settled']='on'
        self.assertEqual(self.render('crispy_cruise_failing'),'True')

    def test_stable_solar_load_is_success_even_if_boost_cooled_faster(self):
        self.states.update({'input_number.crispy_cruise_entry_rate':'-0.4','sensor.crispy_indoor_fast_rate':'0'})
        self.assertEqual(self.render('crispy_cruise_failing'),'False')

    def test_small_noise_not_a_failed_trial(self):
        self.states['sensor.crispy_indoor_fast_rate']='0.08'
        self.assertEqual(self.render('crispy_cruise_failing'),'False')

    def test_changed_intake_invalidates_comparison(self):
        self.states.update({'input_select.crispy_thermal_phase':'cruise','sensor.crispy_hrc_intake_temperature':'20'})
        self.assertEqual(self.render('crispy_cruise_invalidated'),'True')

    def test_forecast_rows_are_sorted_and_duration_is_timestamp_based(self):
        r=self.forecast([(2,22),(.5,18),(1,18)])
        self.assertEqual(r['status'],'closing')
        self.assertEqual(r['window_hours'],2)
        self.assertEqual(r['window_end'],self.now.timestamp()+7200)
        self.assertEqual(r['useful_hours'],1.5)

    def test_open_horizon_is_not_a_deadline(self):
        r=self.forecast([(h,18) for h in range(1,25)])
        self.assertEqual(r['status'],'open_ended')
        self.assertEqual(r['window_hours'],18)
        self.assertIsNone(r['window_end'])

    def test_truncated_data_has_no_closure(self):
        r=self.forecast([(1,18),(2,18)])
        self.assertEqual(r['status'],'open_ended')
        self.assertEqual(r['window_hours'],2)
        self.assertIsNone(r['window_end'])

    def test_gaps_missing_temperature_duplicates_cannot_create_deadlines(self):
        for points in [[(1,18),(4,28)],[(1,18),(2,None),(3,28)],[(1,18),(1,28),(2,28)],[(1,28),(1,18),(2,28)]]:
            r=self.forecast(points)
            self.assertEqual(r['status'],'incomplete')
            self.assertIsNone(r['window_end'])
            self.assertIsNone(r['heat_hours'])

    def test_real_window_end_does_not_drift_with_now(self):
        self.forecast([(1,18),(2,22),(3,28)])
        template=W['crispy_forecast_current_window_end']['state']
        first=self.env.from_string(template).render()
        self.now+=timedelta(minutes=10)
        self.assertEqual(self.env.from_string(template).render(),first)

    def test_window_uses_target_not_current_warm_room(self):
        r=self.forecast([(1,21),(2,22)])
        self.assertEqual(r['status'],'closing')
        self.assertEqual(r['window_hours'],1)

    def test_offset_decays_and_does_not_follow_sun_into_night(self):
        self.states['sensor.crispy_forecast_intake_offset']='9'
        r=self.forecast([(1,18),(2,18),(3,18),(4,18)])
        self.assertEqual(r['window_hours'],1) # bounded +3 offset gives 20 at +1h
        self.attrs[('sun.sun','next_setting')]=(self.now+timedelta(minutes=30)).isoformat()
        r=self.forecast([(1,18),(2,18),(3,18),(4,18)])
        self.assertEqual(r['status'],'open_ended')

    def test_precooling_opt_in_timing_and_better_window(self):
        self.states['sensor.crispy_forecast_heat_risk']='high'
        self.forecast([(1,18),(2,22),(3,27),(4,28)])
        self.states['input_boolean.crispy_precooling']='off'
        self.assertEqual(self.offset(),0)
        self.states['input_boolean.crispy_precooling']='on'
        self.assertEqual(self.offset(),.5)
        self.forecast([(1,17),(2,22),(3,27),(4,28)])
        self.assertEqual(self.offset(),0)
        self.forecast([(h,18 if h<7 else 28) for h in range(1,9)])
        self.assertEqual(self.offset(),0)

    def test_comfort_floor_does_not_raise_user_target(self):
        self.states.update({'input_number.crispy_target':'18.5','input_number.crispy_precool_floor':'18','sensor.crispy_predictive_target_offset':'1'})
        self.assertEqual(self.render('crispy_effective_target'),'18.0')
        self.states['input_number.crispy_target']='17'
        self.assertEqual(self.render('crispy_effective_target'),'17.0')

    def test_reason_does_not_credit_predictor_on_tie(self):
        self.states.update({'sensor.crispy_requested_fan_mode':'medium','sensor.crispy_raw_fan_demand':'medium','input_select.crispy_held_fan_demand':'medium','sensor.crispy_applied_thermal_demand':'medium','sensor.crispy_base_thermal_demand':'medium','sensor.crispy_predictive_thermal_demand':'medium','sensor.crispy_predictive_aggression':'aggressive'})
        self.assertEqual(self.render('crispy_control_reason'),'Cooling above target')

    def test_source_report_freshness_ignores_unchanged_alias_values(self):
        class States:
            def __call__(obj,key): return self.states.get(key,'unknown')
            def __getitem__(obj,key): return sources.get(key)
        sources={}
        for alias in ['indoor_temperature','intake_temperature','supply_temperature','supply_flow','exhaust_flow']:
            source='sensor.source_'+alias
            self.attrs[('sensor.crispy_hrc_'+alias,'source_entity')]=source
            self.states[source]='20'
            sources[source]=SimpleNamespace(last_reported=self.now-timedelta(seconds=30), last_updated=self.now-timedelta(hours=5))
        self.env.globals['states']=States()
        self.assertEqual(float(self.render('crispy_data_age')),0.5)
        sources['sensor.source_supply_flow'].last_reported=self.now-timedelta(minutes=11)
        self.assertEqual(float(self.render('crispy_data_age')),11)
        sources['sensor.source_supply_flow'].last_reported=self.now+timedelta(minutes=1)
        self.assertEqual(float(self.render('crispy_data_age')),999)

    def test_near_target_high_dwell_does_not_defeat_thermal_cap(self):
        self.states.update({'sensor.crispy_hrc_indoor_temperature':'20.31','sensor.crispy_raw_fan_demand':'medium','input_select.crispy_held_fan_demand':'high'})
        self.assertEqual(self.render('crispy_requested_fan_mode'),'medium')
        self.states['sensor.crispy_raw_fan_demand']='high' # independent drying demand still wins
        self.assertEqual(self.render('crispy_requested_fan_mode'),'high')

    def test_restart_discards_old_trial_but_keeps_baseline_until_next_trial(self):
        manager=next(a for a in CORE['automation'] if a['id']=='crispy_thermal_phase_manager')
        template=next(a['variables']['next_phase'] for a in manager['actions'] if 'next_phase' in a.get('variables',{}))
        self.states.update({'input_select.crispy_thermal_phase':'cruise','binary_sensor.crispy_cruise_failing':'off','binary_sensor.crispy_cruise_invalidated':'off','binary_sensor.crispy_cruise_ready':'off'})
        variables=dict(current='cruise',thermal='medium',owned=False,nonthermal='none')
        self.assertEqual(self.env.from_string(template).render(**variables,trigger=SimpleNamespace(id='state')).strip(),'cruise')
        self.assertEqual(self.env.from_string(template).render(**variables,trigger=SimpleNamespace(id='startup')).strip(),'attack')
        self.states['binary_sensor.crispy_cruise_failing']='on'
        self.assertEqual(self.env.from_string(template).render(**variables,trigger=SimpleNamespace(id='state')).strip(),'attack')
        transition=manager['actions'][-1]['then']
        self.assertEqual(transition[0]['then'][-1]['action'],'timer.start')
        self.assertEqual(transition[0]['then'][-1]['target']['entity_id'],'timer.crispy_cruise_retry')
        self.assertTrue(CORE['timer']['crispy_cruise_retry']['restore'])

    def test_unknown_heat_onset_cannot_precool_or_escalate(self):
        self.states.update({'sensor.crispy_forecast_heat_risk':'extreme','sensor.crispy_forecast_heat_onset_hours':'unavailable'})
        self.assertEqual(self.render('crispy_predictive_aggression'),'normal')
        self.forecast([(1,18),(2,18)])
        self.assertEqual(self.offset(),0)
