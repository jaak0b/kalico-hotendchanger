import pytest

from hotendchanger import (
    ALL_STATES,
    CHANGE_NOOP,
    CHANGE_PROCEED,
    CHANGE_REFUSE,
    DETECT_FAULT,
    DETECT_MOUNTED,
    DETECT_NONE,
    DETECT_NO_PINS,
    DETECT_UNIDENTIFIED,
    LOSS_FIRE,
    LOSS_IDLE,
    LOSS_WAIT,
    PRINT_STATE_PAUSED,
    PRINT_STATE_PRINTING,
    PRINT_STATE_STANDBY,
    REDISCOVER_CANCEL,
    REDISCOVER_RUN,
    REDISCOVER_WAIT,
    STATE_CHANGING,
    STATE_ERROR,
    STATE_READY,
    STATE_UNINITIALIZED,
    STATE_UNKNOWN,
    OffsetLedger,
    begin_change_refusal,
    change_decision,
    classify_print_state,
    mismatch_pauses,
    describe_pin_state,
    describe_pin_states,
    evaluate_rediscovery,
    evaluate_toolhead_loss,
    parse_tool_name,
    resolve_detection,
    state_after_discovery,
    validate_detect_pin_coverage,
    validate_stepper_carrier,
    validate_tool_extruders,
    validate_tool_numbers,
    verify_detected,
    verify_dropoff_released,
)


class TestParseToolName:
    @pytest.mark.parametrize(
        "name,expected",
        [("T0", 0), ("T1", 1), ("T12", 12)],
    )
    def test_accepts_canonical_tool_names(self, name, expected):
        assert parse_tool_name(name) == expected

    @pytest.mark.parametrize(
        "name,expected",
        [("t0", 0), ("t7", 7), ("t12", 12)],
    )
    def test_accepts_lowercased_section_suffixes(self, name, expected):
        assert parse_tool_name(name) == expected

    @pytest.mark.parametrize(
        "name",
        ["", "T", "t", "T-1", "T01", "t01", "T1a", "tool0", "0",
         "T²", "T⁵", "T①"],
    )
    def test_rejects_noncanonical_names(self, name):
        assert parse_tool_name(name) is None


class TestValidateToolNumbers:
    def test_accepts_contiguous_numbering_from_zero(self):
        assert validate_tool_numbers([0, 1, 2]) is None

    def test_accepts_unsorted_contiguous_numbering(self):
        assert validate_tool_numbers([2, 0, 1]) is None

    def test_accepts_single_tool_zero(self):
        assert validate_tool_numbers([0]) is None

    def test_rejects_empty_tool_set(self):
        assert validate_tool_numbers([]) is not None

    def test_rejects_numbering_not_starting_at_zero(self):
        assert validate_tool_numbers([1, 2]) is not None

    def test_rejects_gap_in_numbering(self):
        assert validate_tool_numbers([0, 2]) is not None

    def test_rejects_duplicate_tool_number(self):
        assert validate_tool_numbers([0, 0, 1]) is not None


class TestValidateDetectPinCoverage:
    def test_accepts_no_pins_at_all(self):
        assert validate_detect_pin_coverage([], [0, 1]) is None

    def test_accepts_pins_on_every_tool(self):
        assert validate_detect_pin_coverage([0, 1], [0, 1]) is None

    def test_rejects_partial_coverage_naming_missing_tools(self):
        message = validate_detect_pin_coverage([1], [0, 1, 2])
        assert message is not None
        assert "T0" in message and "T2" in message


class TestValidateToolExtruders:
    def test_accepts_distinct_extruders(self):
        assert validate_tool_extruders({0: "extruder", 1: "extruder1"}) is None

    def test_rejects_two_tools_sharing_an_extruder(self):
        message = validate_tool_extruders(
            {0: "extruder", 1: "extruder1", 2: "extruder1"}
        )
        assert message is not None
        assert "T1" in message and "T2" in message and "extruder1" in message


class TestDescribePinStates:
    def test_single_state_words(self):
        assert describe_pin_state(True) == "triggered"
        assert describe_pin_state(False) == "untriggered"

    def test_reading_lists_tools_in_numeric_order(self):
        assert (
            describe_pin_states({1: False, 0: True})
            == "T0=triggered, T1=untriggered"
        )


class TestResolveDetection:
    def test_exactly_one_untriggered_identifies_that_tool_mounted(self):
        verdict, detected, _ = resolve_detection(
            {0: True, 1: False, 2: True}, None
        )
        assert (verdict, detected) == (DETECT_MOUNTED, 1)

    def test_all_triggered_means_no_tool_mounted(self):
        verdict, detected, _ = resolve_detection({0: True, 1: True}, None)
        assert (verdict, detected) == (DETECT_NONE, None)

    def test_multiple_untriggered_is_a_fault(self):
        verdict, detected, message = resolve_detection(
            {0: False, 1: False, 2: True}, None
        )
        assert (verdict, detected) == (DETECT_FAULT, None)
        assert "T0" in message and "T1" in message

    def test_no_pins_configured_yields_no_pins_verdict(self):
        verdict, detected, _ = resolve_detection({}, None)
        assert (verdict, detected) == (DETECT_NO_PINS, None)

    def test_toolhead_present_confirms_the_single_empty_dock(self):
        verdict, detected, _ = resolve_detection({0: True, 1: False}, True)
        assert (verdict, detected) == (DETECT_MOUNTED, 1)

    def test_toolhead_absent_with_all_docks_full_means_no_tool(self):
        verdict, detected, _ = resolve_detection({0: True, 1: True}, False)
        assert (verdict, detected) == (DETECT_NONE, None)

    def test_toolhead_absent_contradicting_an_empty_dock_is_a_fault(self):
        verdict, detected, message = resolve_detection(
            {0: True, 1: False}, False
        )
        assert (verdict, detected) == (DETECT_FAULT, None)
        assert "T1" in message

    def test_toolhead_present_contradicting_full_docks_is_a_fault(self):
        verdict, detected, _ = resolve_detection({0: True, 1: True}, True)
        assert (verdict, detected) == (DETECT_FAULT, None)

    def test_toolhead_only_present_is_unidentified_mounted(self):
        verdict, detected, message = resolve_detection({}, True)
        assert (verdict, detected) == (DETECT_UNIDENTIFIED, None)
        assert "INITIALIZE_HOTENDCHANGER" in message

    def test_toolhead_only_absent_means_no_tool(self):
        verdict, detected, _ = resolve_detection({}, False)
        assert (verdict, detected) == (DETECT_NONE, None)


class TestStateAfterDiscovery:
    def test_mounted_tool_yields_ready_with_that_tool(self):
        assert state_after_discovery(DETECT_MOUNTED, 2) == (STATE_READY, 2)

    def test_no_tool_mounted_yields_ready_with_no_active_tool(self):
        assert state_after_discovery(DETECT_NONE, None) == (STATE_READY, None)

    def test_fault_yields_unknown(self):
        assert state_after_discovery(DETECT_FAULT, None) == (STATE_UNKNOWN, None)

    def test_no_pins_yields_unknown(self):
        assert state_after_discovery(DETECT_NO_PINS, None) == (
            STATE_UNKNOWN,
            None,
        )

    def test_unidentified_mounted_yields_unknown(self):
        assert state_after_discovery(DETECT_UNIDENTIFIED, None) == (
            STATE_UNKNOWN,
            None,
        )

    def test_unlisted_verdict_raises(self):
        with pytest.raises(ValueError):
            state_after_discovery("half_mounted", None)


class TestVerifyDetected:
    def test_expected_tool_untriggered_and_others_triggered_passes(self):
        assert verify_detected({0: True, 1: False}, None, 1) is None

    def test_expected_tool_still_in_dock_fails(self):
        message = verify_detected({0: True, 1: True}, None, 1)
        assert message is not None
        assert "T1" in message

    def test_wrong_tool_untriggered_fails(self):
        message = verify_detected({0: False, 1: True}, None, 1)
        assert message is not None
        assert "T1" in message and "T0" in message

    def test_multiple_untriggered_fails(self):
        assert verify_detected({0: False, 1: False}, None, 1) is not None

    def test_empty_reading_raises(self):
        with pytest.raises(ValueError):
            verify_detected({}, None, 1)

    def test_docks_and_toolhead_agreeing_passes(self):
        assert verify_detected({0: True, 1: False}, True, 1) is None

    def test_toolhead_absent_after_pickup_fails(self):
        message = verify_detected({0: True, 1: False}, False, 1)
        assert message is not None
        assert "toolhead" in message

    def test_both_checks_failing_names_both(self):
        message = verify_detected({0: True, 1: True}, False, 1)
        assert message is not None
        assert "T1" in message and "toolhead" in message

    def test_toolhead_only_present_passes(self):
        assert verify_detected({}, True, 1) is None

    def test_toolhead_only_absent_fails(self):
        message = verify_detected({}, False, 1)
        assert message is not None
        assert "toolhead" in message


class TestVerifyDropoffReleased:
    def test_no_toolhead_pin_passes(self):
        assert verify_dropoff_released(None) is None

    def test_released_passes(self):
        assert verify_dropoff_released(False) is None

    def test_still_held_fails_naming_the_release(self):
        message = verify_dropoff_released(True)
        assert message is not None
        assert "release" in message


class TestBeginChangeRefusal:
    # Independently chosen matrix: only ready and unknown may start a change.
    REFUSED = {
        STATE_UNINITIALIZED: True,
        STATE_READY: False,
        STATE_CHANGING: True,
        STATE_ERROR: True,
        STATE_UNKNOWN: False,
    }

    @pytest.mark.parametrize("state", ALL_STATES)
    def test_each_declared_state_has_the_expected_verdict(self, state):
        refusal = begin_change_refusal(state)
        assert (refusal is not None) == self.REFUSED[state]

    def test_declared_state_list_matches_the_matrix(self):
        assert sorted(ALL_STATES) == sorted(self.REFUSED)

    def test_unlisted_state_raises(self):
        with pytest.raises(ValueError):
            begin_change_refusal("paused")


class TestChangeDecision:
    @pytest.mark.parametrize(
        "state,active,requested,expected",
        [
            (STATE_READY, None, 1, CHANGE_PROCEED),
            (STATE_READY, 0, 1, CHANGE_PROCEED),
            (STATE_READY, 1, 1, CHANGE_NOOP),
            (STATE_UNKNOWN, None, 0, CHANGE_PROCEED),
            (STATE_UNINITIALIZED, None, 1, CHANGE_REFUSE),
            (STATE_CHANGING, 0, 1, CHANGE_REFUSE),
            (STATE_ERROR, None, 0, CHANGE_REFUSE),
            # Refusal wins over the no-op even for the nominally active tool.
            (STATE_ERROR, 1, 1, CHANGE_REFUSE),
            (STATE_CHANGING, 1, 1, CHANGE_REFUSE),
        ],
    )
    def test_decision_matrix(self, state, active, requested, expected):
        decision, _ = change_decision(state, active, requested)
        assert decision == expected

    def test_refusal_carries_a_message(self):
        decision, message = change_decision(STATE_ERROR, None, 0)
        assert decision == CHANGE_REFUSE
        assert message

    def test_noop_carries_a_message_naming_the_tool(self):
        decision, message = change_decision(STATE_READY, 2, 2)
        assert decision == CHANGE_NOOP
        assert "T2" in message


class TestValidateStepperCarrier:
    def test_exactly_one_stepper_carrier_passes(self):
        assert validate_stepper_carrier([0]) is None

    def test_no_stepper_carrier_fails(self):
        message = validate_stepper_carrier([])
        assert message is not None
        assert "step_pin" in message

    def test_multiple_stepper_carriers_fails_naming_them(self):
        message = validate_stepper_carrier([0, 2])
        assert message is not None
        assert "T0" in message and "T2" in message


class TestEvaluateToolheadLoss:
    def default_args(self, **overrides):
        args = {
            "toolhead_present": False,
            "absent_since": 100.0,
            "now": 102.0,
            "settle_time": 1.0,
            "changer_state": STATE_READY,
            "print_state": PRINT_STATE_PRINTING,
            "active_tool": 1,
            "already_fired": False,
        }
        args.update(overrides)
        return args

    def test_debounced_absence_while_printing_fires(self):
        assert evaluate_toolhead_loss(**self.default_args()) == LOSS_FIRE

    def test_present_pin_is_idle(self):
        args = self.default_args(toolhead_present=True)
        assert evaluate_toolhead_loss(**args) == LOSS_IDLE

    def test_no_recorded_absence_is_idle(self):
        args = self.default_args(absent_since=None)
        assert evaluate_toolhead_loss(**args) == LOSS_IDLE

    def test_already_fired_loss_event_stays_idle(self):
        args = self.default_args(already_fired=True)
        assert evaluate_toolhead_loss(**args) == LOSS_IDLE

    def test_absence_shorter_than_the_window_waits(self):
        args = self.default_args(now=100.5)
        assert evaluate_toolhead_loss(**args) == LOSS_WAIT

    def test_absence_exactly_at_the_window_fires(self):
        args = self.default_args(now=101.0)
        assert evaluate_toolhead_loss(**args) == LOSS_FIRE

    @pytest.mark.parametrize(
        "state", [s for s in ALL_STATES if s != STATE_READY]
    )
    def test_every_non_ready_state_suspends_the_monitor(self, state):
        args = self.default_args(changer_state=state)
        assert evaluate_toolhead_loss(**args) == LOSS_IDLE

    def test_no_active_tool_is_idle(self):
        args = self.default_args(active_tool=None)
        assert evaluate_toolhead_loss(**args) == LOSS_IDLE

    @pytest.mark.parametrize(
        "print_state", [PRINT_STATE_PAUSED, PRINT_STATE_STANDBY]
    )
    def test_only_an_active_print_arms_the_monitor(self, print_state):
        args = self.default_args(print_state=print_state)
        assert evaluate_toolhead_loss(**args) == LOSS_IDLE

    def test_unlisted_changer_state_raises(self):
        args = self.default_args(changer_state="rebooting")
        with pytest.raises(ValueError):
            evaluate_toolhead_loss(**args)

    def test_unlisted_print_state_raises(self):
        args = self.default_args(print_state="resuming")
        with pytest.raises(ValueError):
            evaluate_toolhead_loss(**args)


class TestEvaluateRediscovery:
    def default_args(self, **overrides):
        args = {
            "last_edge": 100.0,
            "now": 102.0,
            "settle_time": 1.0,
            "changer_state": STATE_READY,
            "print_state": PRINT_STATE_STANDBY,
        }
        args.update(overrides)
        return args

    def test_settled_edge_while_idle_runs(self):
        assert evaluate_rediscovery(**self.default_args()) == REDISCOVER_RUN

    def test_settled_edge_while_paused_runs(self):
        args = self.default_args(print_state=PRINT_STATE_PAUSED)
        assert evaluate_rediscovery(**args) == REDISCOVER_RUN

    def test_recent_edge_waits(self):
        args = self.default_args(now=100.5)
        assert evaluate_rediscovery(**args) == REDISCOVER_WAIT

    def test_no_recorded_edge_cancels(self):
        args = self.default_args(last_edge=None)
        assert evaluate_rediscovery(**args) == REDISCOVER_CANCEL

    def test_change_in_progress_cancels(self):
        args = self.default_args(changer_state=STATE_CHANGING)
        assert evaluate_rediscovery(**args) == REDISCOVER_CANCEL

    def test_active_print_cancels(self):
        args = self.default_args(print_state=PRINT_STATE_PRINTING)
        assert evaluate_rediscovery(**args) == REDISCOVER_CANCEL

    @pytest.mark.parametrize(
        "state",
        [STATE_READY, STATE_UNKNOWN, STATE_ERROR, STATE_UNINITIALIZED],
    )
    def test_every_non_changing_state_allows_rediscovery(self, state):
        args = self.default_args(changer_state=state)
        assert evaluate_rediscovery(**args) == REDISCOVER_RUN

    def test_unlisted_changer_state_raises(self):
        args = self.default_args(changer_state="rebooting")
        with pytest.raises(ValueError):
            evaluate_rediscovery(**args)

    def test_unlisted_print_state_raises(self):
        args = self.default_args(print_state="resuming")
        with pytest.raises(ValueError):
            evaluate_rediscovery(**args)


class TestClassifyPrintState:
    @pytest.mark.parametrize(
        "stats_state,expected",
        [
            ("printing", PRINT_STATE_PRINTING),
            ("paused", PRINT_STATE_PAUSED),
            ("standby", PRINT_STATE_STANDBY),
            ("complete", PRINT_STATE_STANDBY),
            ("cancelled", PRINT_STATE_STANDBY),
            ("error", PRINT_STATE_STANDBY),
        ],
    )
    def test_every_print_stats_state_maps(self, stats_state, expected):
        assert classify_print_state(False, stats_state, False) == expected

    def test_pause_resume_paused_wins_over_printing(self):
        assert (
            classify_print_state(True, "printing", True) == PRINT_STATE_PAUSED
        )

    def test_no_print_stats_falls_back_to_sd_activity(self):
        assert classify_print_state(False, None, True) == PRINT_STATE_PRINTING
        assert classify_print_state(False, None, False) == PRINT_STATE_STANDBY

    def test_unlisted_print_stats_state_raises(self):
        with pytest.raises(ValueError):
            classify_print_state(False, "interrupted", False)


class TestMismatchPauses:
    def test_printing_pauses(self):
        assert mismatch_pauses(PRINT_STATE_PRINTING) is True

    def test_paused_raises_instead(self):
        assert mismatch_pauses(PRINT_STATE_PAUSED) is False

    def test_standby_raises_instead(self):
        assert mismatch_pauses(PRINT_STATE_STANDBY) is False

    def test_unlisted_print_state_raises(self):
        with pytest.raises(ValueError):
            mismatch_pauses("resuming")


class TestOffsetLedger:
    def test_first_apply_moves_origin_by_the_full_tool_offset(self):
        ledger = OffsetLedger()
        new_origin, drift = ledger.plan((0.0, 0.0, 0.0), (0.4, -0.2, 0.15))
        assert new_origin == (0.4, -0.2, 0.15)
        assert drift == (0.0, 0.0, 0.0)

    def test_switching_tools_replaces_only_the_tool_component(self):
        ledger = OffsetLedger()
        ledger.commit((1.0, 2.0, 3.0), (1.0, 2.0, 3.0))
        # hand-derived: origin (1.0, 2.0, 3.05) carries a 0.05 babystep on Z;
        # swapping the tool component (1.0, 2.0, 3.0) for (4.0, 4.0, 4.0)
        # must land on (4.0, 4.0, 4.05)
        new_origin, _ = ledger.plan((1.0, 2.0, 3.05), (4.0, 4.0, 4.0))
        assert new_origin == pytest.approx((4.0, 4.0, 4.05), abs=1e-12)

    def test_clearing_leaves_only_the_outside_component(self):
        ledger = OffsetLedger()
        ledger.commit((0.5, -1.5, 0.25), (0.5, -1.5, 0.25))
        # hand-derived: origin (0.5, -1.5, 0.30) minus the tool component
        # (0.5, -1.5, 0.25) leaves the 0.05 babystep on Z
        new_origin, _ = ledger.plan((0.5, -1.5, 0.30), (0.0, 0.0, 0.0))
        assert new_origin == pytest.approx((0.0, 0.0, 0.05), abs=1e-12)

    def test_same_target_with_unchanged_origin_plans_no_move(self):
        ledger = OffsetLedger()
        ledger.commit((1.0, 2.0, 3.0), (1.0, 2.0, 3.0))
        new_origin, drift = ledger.plan((1.0, 2.0, 3.0), (1.0, 2.0, 3.0))
        assert new_origin == (1.0, 2.0, 3.0)
        assert drift == (0.0, 0.0, 0.0)

    def test_drift_reports_the_outside_change_since_the_last_command(self):
        ledger = OffsetLedger()
        ledger.commit((1.0, 2.0, 3.0), (1.0, 2.0, 3.0))
        _, drift = ledger.plan((1.0, 2.0, 3.05), (0.0, 0.0, 0.0))
        assert drift == pytest.approx((0.0, 0.0, 0.05), abs=1e-12)

    def test_drift_is_zero_before_the_first_command(self):
        ledger = OffsetLedger()
        _, drift = ledger.plan((0.0, 0.0, 0.7), (0.0, 0.0, 0.0))
        assert drift == (0.0, 0.0, 0.0)
