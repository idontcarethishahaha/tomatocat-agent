from __future__ import annotations

from collections.abc import Awaitable, Callable

from tomatocat.bus import EventBus
from tomatocat.lifecycle.types import (
    AfterReasoningCtx,
    AfterStepCtx,
    AfterTurnCtx,
    BeforeReasoningCtx,
    BeforeStepCtx,
    BeforeTurnCtx,
    PromptRenderCtx,
)


class TurnLifecycle:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    def on_before_turn(
        self,
        handler: Callable[
            [BeforeTurnCtx],
            Awaitable[BeforeTurnCtx | None] | BeforeTurnCtx | None,
        ],
    ) -> None:
        self._bus.on(BeforeTurnCtx, handler)

    def on_before_reasoning(
        self,
        handler: Callable[
            [BeforeReasoningCtx],
            Awaitable[BeforeReasoningCtx | None] | BeforeReasoningCtx | None,
        ],
    ) -> None:
        self._bus.on(BeforeReasoningCtx, handler)

    def on_before_step(
        self,
        handler: Callable[
            [BeforeStepCtx],
            Awaitable[BeforeStepCtx | None] | BeforeStepCtx | None,
        ],
    ) -> None:
        self._bus.on(BeforeStepCtx, handler)

    def on_prompt_render(
        self,
        handler: Callable[
            [PromptRenderCtx],
            Awaitable[PromptRenderCtx | None] | PromptRenderCtx | None,
        ],
    ) -> None:
        self._bus.on(PromptRenderCtx, handler)

    def on_after_reasoning(
        self,
        handler: Callable[
            [AfterReasoningCtx],
            Awaitable[AfterReasoningCtx | None] | AfterReasoningCtx | None,
        ],
    ) -> None:
        self._bus.on(AfterReasoningCtx, handler)

    def on_after_step(
        self,
        handler: Callable[[AfterStepCtx], Awaitable[None] | None],
    ) -> None:
        self._bus.on(AfterStepCtx, handler)

    def on_after_turn(
        self,
        handler: Callable[[AfterTurnCtx], Awaitable[None] | None],
    ) -> None:
        self._bus.on(AfterTurnCtx, handler)