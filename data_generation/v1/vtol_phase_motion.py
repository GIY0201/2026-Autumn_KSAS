"""State-gated execution for the opt-in VTOL phase-aware experiment policy."""

from __future__ import annotations

from dataclasses import dataclass, field

from contracts.v1.validation import DURATION_S

from .bounded_motion_scenario import PhaseMotionScenario, PhaseMotionTarget, ScheduledMotionCommand
from .records import MotionEvent

_EPSILON = 1e-12


def _endpoint_flat_blend(progress: float) -> float:
    """Return a quintic reference blend with zero slope at both endpoints."""
    clipped = min(max(progress, 0.0), 1.0)
    return clipped**3 * (10.0 + clipped * (-15.0 + 6.0 * clipped))


@dataclass(slots=True)
class PhaseMotionRuntime:
    """Advance a sampled phase plan only after its state and dwell conditions hold."""

    scenario: PhaseMotionScenario
    _phase_index: int = field(init=False, default=0)
    _phase_start_s: float = field(init=False, default=0.0)
    _settled_since_s: float | None = field(init=False, default=None)
    _previous_target: tuple[float, float, float] = field(init=False)
    _records: list[MotionEvent] = field(init=False, default_factory=list)
    _failure_reason: str | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self._previous_target = (
            self.scenario.initial_speed_mps,
            self.scenario.initial_vertical_speed_mps,
            self.scenario.initial_turn_rate_rad_s,
        )

    @property
    def is_finished(self) -> bool:
        return self._phase_index >= len(self.scenario.targets)

    @property
    def has_failed(self) -> bool:
        return self._failure_reason is not None

    @property
    def failure_reason(self) -> str | None:
        return self._failure_reason

    @property
    def event_records(self) -> tuple[MotionEvent, ...]:
        return tuple(self._records)

    def _active_target(self) -> PhaseMotionTarget:
        if self.is_finished:
            return self.scenario.targets[-1]
        return self.scenario.targets[self._phase_index]

    def _target_values(self, time_s: float) -> tuple[float, float, float]:
        target = self._active_target()
        if self.is_finished:
            return (
                target.target_speed_mps,
                target.target_vertical_speed_mps,
                target.target_turn_rate_rad_s,
            )
        blend = _endpoint_flat_blend(
            (time_s - self._phase_start_s) / self.scenario.policy.reference_blend_s
        )
        final = (
            target.target_speed_mps,
            target.target_vertical_speed_mps,
            target.target_turn_rate_rad_s,
        )
        return tuple(
            start + blend * (end - start)
            for start, end in zip(self._previous_target, final, strict=True)
        )

    def command_at(self, time_s: float) -> ScheduledMotionCommand:
        """Return the smooth current reference and the exact next timeout boundary."""
        target = self._active_target()
        target_speed_mps, target_vertical_speed_mps, target_turn_rate_rad_s = self._target_values(
            time_s
        )
        if self.is_finished:
            end_s = DURATION_S
        else:
            end_s = min(
                DURATION_S,
                self._phase_start_s
                + target.reference_duration_s
                + self.scenario.policy.max_extension_s,
            )
        return ScheduledMotionCommand(
            index=min(self._phase_index, len(self.scenario.targets) - 1),
            mode=target.mode,
            start_s=self._phase_start_s,
            end_s=end_s,
            target_speed_mps=target_speed_mps,
            target_vertical_speed_mps=target_vertical_speed_mps,
            target_turn_rate_rad_s=target_turn_rate_rad_s,
            was_cut_off=False,
        )

    def observe(self, time_s: float, *, target_reached: bool) -> None:
        """Record a state-gated completion or an explicit pre-cutoff timeout."""
        if self.is_finished or self.has_failed:
            return
        target = self._active_target()
        minimum_end_s = self._phase_start_s + target.reference_duration_s
        if time_s >= minimum_end_s - _EPSILON and target_reached:
            if self._settled_since_s is None:
                self._settled_since_s = time_s
            if time_s >= self._settled_since_s + self.scenario.policy.settling_dwell_s - _EPSILON:
                self._records.append(
                    MotionEvent(
                        event_id=f"segment-{self._phase_index:03d}-{target.mode.name}",
                        start_s=self._phase_start_s,
                        end_s=time_s,
                        trigger_kind="phase_aware_simulation_design_command",
                        completed=True,
                        completion_reason="target_response_tolerance_met_after_minimum_and_dwell",
                    )
                )
                self._previous_target = (
                    target.target_speed_mps,
                    target.target_vertical_speed_mps,
                    target.target_turn_rate_rad_s,
                )
                self._phase_index += 1
                self._phase_start_s = time_s
                self._settled_since_s = None
                return
        else:
            self._settled_since_s = None

        deadline_s = minimum_end_s + self.scenario.policy.max_extension_s
        if time_s >= deadline_s - _EPSILON and time_s < DURATION_S - _EPSILON:
            self._records.append(
                MotionEvent(
                    event_id=f"segment-{self._phase_index:03d}-{target.mode.name}",
                    start_s=self._phase_start_s,
                    end_s=time_s,
                    trigger_kind="phase_aware_simulation_design_command",
                    completed=False,
                    completion_reason="phase_target_timeout",
                )
            )
            self._failure_reason = f"phase_target_timeout:{target.mode.name}"

    def finalize_episode(self, time_s: float) -> None:
        """Record an unfinished phase at the fixed observation-window boundary."""
        if self.is_finished or self.has_failed:
            return
        if abs(time_s - DURATION_S) > _EPSILON:
            raise ValueError("phase-aware finalization must occur at the fixed episode boundary")
        target = self._active_target()
        self._records.append(
            MotionEvent(
                event_id=f"segment-{self._phase_index:03d}-{target.mode.name}",
                start_s=self._phase_start_s,
                end_s=time_s,
                trigger_kind="phase_aware_simulation_design_command",
                completed=False,
                completion_reason="episode_window_cut_off",
            )
        )


__all__ = ["PhaseMotionRuntime"]
