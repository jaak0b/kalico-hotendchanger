import pytest

from hotendchanger import (
    ALL_STATES,
    DETECT_FAULT,
    DETECT_MOUNTED,
    DETECT_NONE,
    DETECT_NO_PINS,
    STATE_CHANGING,
    STATE_ERROR,
    STATE_READY,
    STATE_UNINITIALIZED,
    STATE_UNKNOWN,
    OffsetLedger,
    begin_change_refusal,
    parse_tool_name,
    resolve_detection,
    state_after_discovery,
    validate_tool_numbers,
    verify_mounted,
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
        ["", "T", "t", "T-1", "T01", "t01", "T1a", "tool0", "0"],
    )
    def test_rejects_noncanonical_names(self, name):
        assert parse_tool_name(name) is None


class TestValidateToolNumbers:
    def test_accepts_contiguous_numbering_from_zero(self):
        assert validate_tool_numbers([0, 1, 2]) is None

    def test_accepts_single_tool_zero(self):
        assert validate_tool_numbers([0]) is None

    def test_rejects_empty_tool_set(self):
        assert validate_tool_numbers([]) is not None

    def test_rejects_numbering_not_starting_at_zero(self):
        assert validate_tool_numbers([1, 2]) is not None

    def test_rejects_gap_in_numbering(self):
        assert validate_tool_numbers([0, 2]) is not None


class TestResolveDetection:
    def test_exactly_one_untriggered_identifies_that_tool_mounted(self):
        verdict, mounted, _ = resolve_detection(
            {0: True, 1: False, 2: True}, [0, 1, 2]
        )
        assert (verdict, mounted) == (DETECT_MOUNTED, 1)

    def test_all_triggered_with_full_coverage_means_no_tool_mounted(self):
        verdict, mounted, _ = resolve_detection({0: True, 1: True}, [0, 1])
        assert (verdict, mounted) == (DETECT_NONE, None)

    def test_multiple_untriggered_is_a_fault(self):
        verdict, mounted, message = resolve_detection(
            {0: False, 1: False, 2: True}, [0, 1, 2]
        )
        assert (verdict, mounted) == (DETECT_FAULT, None)
        assert "T0" in message and "T1" in message

    def test_no_pins_configured_yields_no_pins_verdict(self):
        verdict, mounted, _ = resolve_detection({}, [0, 1])
        assert (verdict, mounted) == (DETECT_NO_PINS, None)

    def test_all_triggered_with_partial_coverage_is_a_fault(self):
        verdict, mounted, message = resolve_detection({1: True}, [0, 1])
        assert (verdict, mounted) == (DETECT_FAULT, None)
        assert "T0" in message

    def test_untriggered_pin_with_partial_coverage_still_identifies_tool(self):
        verdict, mounted, _ = resolve_detection({1: False}, [0, 1])
        assert (verdict, mounted) == (DETECT_MOUNTED, 1)

    def test_unreported_pin_state_is_a_fault(self):
        verdict, mounted, message = resolve_detection(
            {0: None, 1: True}, [0, 1]
        )
        assert (verdict, mounted) == (DETECT_FAULT, None)
        assert "T0" in message


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

    def test_unlisted_verdict_raises(self):
        with pytest.raises(ValueError):
            state_after_discovery("half_mounted", None)


class TestVerifyMounted:
    def test_expected_tool_untriggered_and_others_triggered_passes(self):
        ok, _ = verify_mounted({0: True, 1: False}, 1)
        assert ok is True

    def test_expected_tool_still_in_dock_fails(self):
        ok, detail = verify_mounted({0: True, 1: True}, 1)
        assert ok is False
        assert "T1" in detail

    def test_wrong_tool_untriggered_fails(self):
        ok, _ = verify_mounted({0: False, 1: True}, 1)
        assert ok is False

    def test_multiple_untriggered_fails(self):
        ok, _ = verify_mounted({0: False, 1: False}, 1)
        assert ok is False

    def test_expected_tool_without_pin_passes_when_all_docks_triggered(self):
        ok, _ = verify_mounted({1: True, 2: True}, 0)
        assert ok is True

    def test_expected_tool_without_pin_fails_when_a_dock_is_empty(self):
        ok, _ = verify_mounted({1: False, 2: True}, 0)
        assert ok is False

    def test_unreported_pin_state_fails(self):
        ok, detail = verify_mounted({0: None, 1: False}, 1)
        assert ok is False
        assert "T0" in detail


class TestBeginChangeRefusal:
    def test_ready_allows_a_change(self):
        assert begin_change_refusal(STATE_READY) is None

    def test_unknown_allows_a_change(self):
        assert begin_change_refusal(STATE_UNKNOWN) is None

    def test_uninitialized_refuses(self):
        assert begin_change_refusal(STATE_UNINITIALIZED) is not None

    def test_changing_refuses(self):
        assert begin_change_refusal(STATE_CHANGING) is not None

    def test_error_refuses(self):
        assert begin_change_refusal(STATE_ERROR) is not None

    def test_every_declared_state_is_handled(self):
        for state in ALL_STATES:
            begin_change_refusal(state)

    def test_unlisted_state_raises(self):
        with pytest.raises(ValueError):
            begin_change_refusal("paused")


class TestOffsetLedger:
    def test_first_apply_returns_the_full_tool_offset(self):
        ledger = OffsetLedger()
        assert ledger.delta_to((0.4, -0.2, 0.15)) == (0.4, -0.2, 0.15)

    def test_switching_tools_returns_only_the_difference(self):
        ledger = OffsetLedger()
        ledger.delta_to((1.0, 2.0, 3.0))
        # hand-derived: (4.0, 4.0, 4.0) minus (1.0, 2.0, 3.0)
        assert ledger.delta_to((4.0, 4.0, 4.0)) == (3.0, 2.0, 1.0)

    def test_clear_returns_the_negation_of_the_applied_offset(self):
        ledger = OffsetLedger()
        ledger.delta_to((0.5, -1.5, 0.25))
        assert ledger.clear() == (-0.5, 1.5, -0.25)

    def test_clear_when_nothing_applied_is_a_zero_delta(self):
        ledger = OffsetLedger()
        assert ledger.clear() == (0.0, 0.0, 0.0)

    def test_apply_clear_sequence_sums_to_zero_leaving_babystep_intact(self):
        # A babystep offset lives outside the ledger; the ledger's deltas are
        # applied on top of it, so the babystep survives exactly when the
        # deltas over a full apply/switch/clear cycle sum to zero.
        babystep = (0.0, 0.0, 0.05)
        applied = list(babystep)
        ledger = OffsetLedger()
        for target in ((1.0, 2.0, 3.0), (4.0, 4.0, 4.0)):
            delta = ledger.delta_to(target)
            applied = [a + d for a, d in zip(applied, delta)]
        delta = ledger.clear()
        applied = [a + d for a, d in zip(applied, delta)]
        # tolerance: double precision rounding over three delta additions
        assert applied == pytest.approx([0.0, 0.0, 0.05], abs=1e-12)
