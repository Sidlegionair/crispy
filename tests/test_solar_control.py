"""Render the actual YAML predicates; no live HA or RF calls.

Run: python -m unittest discover -s tests -v
Dependencies: PyYAML, Jinja2. These tests do not emulate HA's event scheduler.
"""
from pathlib import Path
import unittest

import yaml
from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]
CORE = yaml.safe_load((ROOT / "packages/crispy.yaml").read_text())
ENTITIES = {
    entity["unique_id"]: entity
    for block in CORE["template"]
    for domain in ("sensor", "binary_sensor")
    for entity in block.get(domain, [])
}


class SolarControl(unittest.TestCase):
    def setUp(self):
        self.states = {
            "input_boolean.crispy_mode": "on",
            "input_boolean.crispy_predictive_control": "on",
            "input_select.crispy_control_strategy": "Auto",
            "input_select.crispy_thermal_phase": "attack",
            "binary_sensor.crispy_sensors_healthy": "on",
            "binary_sensor.crispy_momentum_confident": "on",
            "binary_sensor.crispy_hrc_bypass_open": "on",
            "binary_sensor.crispy_cooling_attempt_settled": "on",
            "binary_sensor.crispy_cooling_watchdog_blocked": "off",
            "sensor.crispy_thermal_demand": "high",
            "sensor.crispy_hrc_fan_mode": "high",
            "sensor.crispy_indoor_fast_rate": "0",
            "sensor.crispy_cooling_power": "200",
            "sensor.crispy_hrc_indoor_temperature": "24",
            "sensor.crispy_hrc_supply_temperature": "22",
            "sensor.crispy_hrc_intake_temperature": "20",
            "sensor.crispy_hrc_supply_flow": "60",
            "sensor.crispy_effective_target": "20",
            "input_number.crispy_min_intake": "12",
            "input_number.crispy_cruise_entry_rate": "0",
            "input_number.crispy_watchdog_intake": "21",
            "timer.crispy_cooling_retry": "idle",
            "timer.crispy_cruise_retry": "idle",
            "input_boolean.crispy_fan_override_active": "on",
            "input_boolean.crispy_external_override": "off",
            "binary_sensor.crispy_cruise_settled": "on",
            "binary_sensor.crispy_thermal_call": "on",
            "input_number.crispy_cruise_entry_rate": "0",
        }
        self.env = Environment(undefined=StrictUndefined)
        self.env.globals.update(
            states=lambda key: self.states.get(key, "unknown"),
            is_state=lambda key, value: self.states.get(key) == value,
            has_value=lambda key: self.states.get(key, "unknown") not in ("unknown", "unavailable"),
        )

    def render(self, name):
        return self.env.from_string(ENTITIES[name]["state"]).render().strip()

    def check(self, name, expected):
        self.assertEqual(self.render("crispy_" + name), str(expected))

    def test_stable_room_under_solar_can_cruise(self):
        self.check("cruise_ready", True)

    def test_cooling_room_can_cruise(self):
        self.states["sensor.crispy_indoor_fast_rate"] = "-0.1"
        self.check("cruise_ready", True)

    def test_warming_room_keeps_attack_but_not_watchdog(self):
        self.states["sensor.crispy_indoor_fast_rate"] = "0.3"
        self.check("cruise_ready", False)
        self.check("cooling_delivery_failed", False)

    def test_full_send_cannot_cruise(self):
        self.states["input_select.crispy_control_strategy"] = "Full Send"
        self.check("cruise_ready", False)
        self.states["input_select.crispy_thermal_phase"] = "cruise"
        self.assertEqual(self.render("crispy_applied_thermal_demand"), "high")

    def test_missing_momentum_cannot_qualify(self):
        self.states["binary_sensor.crispy_momentum_confident"] = "off"
        self.check("cruise_ready", False)

    def test_stable_cruise_is_success(self):
        self.states["input_select.crispy_thermal_phase"] = "cruise"
        self.check("cruise_failing", False)

    def test_small_positive_noise_does_not_fail_cruise(self):
        self.states["input_select.crispy_thermal_phase"] = "cruise"
        self.states["sensor.crispy_indoor_fast_rate"] = "0.02"
        self.check("cruise_failing", False)

    def test_sustained_rebound_reattacks(self):
        self.states["input_select.crispy_thermal_phase"] = "cruise"
        self.states["sensor.crispy_indoor_fast_rate"] = "0.20"
        self.check("cruise_failing", True)

    def test_slower_cooling_loses_progress_above_target(self):
        self.states["input_select.crispy_thermal_phase"] = "cruise"
        self.states["input_number.crispy_cruise_entry_rate"] = "-0.3"
        self.states["sensor.crispy_indoor_fast_rate"] = "-0.05"
        self.check("cruise_failing", True)

    def test_marginal_cooling_is_not_watchdog_failure(self):
        self.states["sensor.crispy_hrc_supply_temperature"] = "23.95"
        self.states["sensor.crispy_cooling_power"] = "4"
        self.check("cooling_delivery_failed", False)
        self.states["input_select.crispy_thermal_phase"] = "cruise"
        self.check("cruise_failing", False)

    def test_equal_or_warmer_supply_fails_after_settling(self):
        for supply in ("24", "24.5"):
            self.states["sensor.crispy_hrc_supply_temperature"] = supply
            self.check("cooling_delivery_failed", True)

    def test_negligible_airflow_fails(self):
        self.states["sensor.crispy_hrc_supply_flow"] = "0"
        self.check("cooling_delivery_failed", True)

    def test_settling_gates_failure(self):
        self.states["sensor.crispy_hrc_supply_temperature"] = "25"
        self.states["binary_sensor.crispy_cooling_attempt_settled"] = "off"
        self.check("cooling_delivery_failed", False)

    def test_fault_gates_failure(self):
        self.states["sensor.crispy_hrc_supply_temperature"] = "25"
        self.states["binary_sensor.crispy_sensors_healthy"] = "off"
        self.check("cooling_delivery_failed", False)

    def test_settling_requires_live_attempt(self):
        for key, value in (
            ("input_boolean.crispy_mode", "off"),
            ("input_select.crispy_thermal_phase", "cruise"),
            ("binary_sensor.crispy_hrc_bypass_open", "off"),
            ("sensor.crispy_hrc_fan_mode", "unknown"),
        ):
            old = self.states[key]
            self.states[key] = value
            self.check("cooling_attempt_settled", False)
            self.states[key] = old

    def test_retry_ends_on_sustained_intake_improvement(self):
        self.states["timer.crispy_cooling_retry"] = "active"
        self.check("cooling_retry_improved", True)
        self.states["sensor.crispy_hrc_intake_temperature"] = "20.5"
        self.check("cooling_retry_improved", False)

    def test_retry_respects_guard_and_live_advantage(self):
        self.states["timer.crispy_cooling_retry"] = "active"
        self.states["sensor.crispy_hrc_intake_temperature"] = "11"
        self.check("cooling_retry_improved", False)
        self.states["sensor.crispy_hrc_intake_temperature"] = "20"
        self.states["sensor.crispy_hrc_indoor_temperature"] = "20.2"
        self.check("cooling_retry_improved", False)

    def test_timer_expiry_releases_lockout(self):
        self.states["timer.crispy_cooling_retry"] = "active"
        self.check("cooling_watchdog_blocked", True)
        self.states["timer.crispy_cooling_retry"] = "idle"
        self.check("cooling_watchdog_blocked", False)

    def test_moisture_remains_allowed_during_lockout(self):
        self.states.update({
            "binary_sensor.crispy_cooling_watchdog_blocked": "on",
            "sensor.crispy_applied_thermal_demand": "none",
            "sensor.crispy_moisture_demand": "medium",
            "sensor.crispy_laundry_demand": "none",
        })
        self.assertEqual(self.render("crispy_applied_thermal_demand"), "none")
        self.assertEqual(self.render("crispy_raw_fan_demand"), "medium")

    def test_delays_and_restore_configuration(self):
        for name, duration in (
            ("cruise_ready", "00:15:00"),
            ("cruise_failing", "00:05:00"),
            ("cooling_attempt_settled", "00:05:00"),
            ("cooling_delivery_failed", "00:03:00"),
            ("cooling_retry_improved", "00:02:00"),
        ):
            self.assertEqual(ENTITIES["crispy_" + name]["delay_on"], duration)
        self.assertTrue(CORE["timer"]["crispy_cooling_retry"]["restore"])
        self.assertEqual(CORE["timer"]["crispy_cooling_retry"]["duration"], "00:10:00")

    def test_cruise_baseline_is_written_before_phase(self):
        manager = next(a for a in CORE["automation"] if a["id"] == "crispy_thermal_phase_manager")
        transition = manager["actions"][-1]["then"]
        self.assertEqual(transition[1]["then"][1]["target"]["entity_id"], "input_number.crispy_cruise_entry_rate")
        self.assertEqual(transition[-1]["target"]["entity_id"], "input_select.crispy_thermal_phase")

    def test_watchdog_reconciles_on_raw_demand_change(self):
        dwell = next(a for a in CORE["automation"] if a["id"] == "crispy_fan_demand_dwell")
        self.assertTrue(any(t.get("entity_id") == "sensor.crispy_raw_fan_demand" for t in dwell["triggers"]))
        predicate = dwell["actions"][-1]["choose"][1]["conditions"][0]["value_template"]
        self.states["binary_sensor.crispy_cooling_watchdog_blocked"] = "on"
        # This must work on a later raw-demand update, not just watchdog's edge.
        self.assertEqual(self.env.from_string(predicate).render(desired_rank=0, held_rank=3).strip(), "True")

    def test_yaml_keys_and_jinja_syntax(self):
        class UniqueLoader(yaml.SafeLoader):
            pass

        def mapping(loader, node, deep=False):
            result = {}
            for key_node, value_node in node.value:
                key = loader.construct_object(key_node, deep=deep)
                if key in result:
                    raise ValueError(f"Duplicate YAML key: {key}")
                result[key] = loader.construct_object(value_node, deep=deep)
            return result

        UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)

        def walk(value):
            if isinstance(value, dict):
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
            elif isinstance(value, str) and ("{{" in value or "{%" in value):
                self.env.parse(value)

        for path in [*ROOT.glob("packages/*.yaml"), *ROOT.glob("dashboards/*.yaml")]:
            walk(yaml.load(path.read_text(), Loader=UniqueLoader))


if __name__ == "__main__":
    unittest.main()
