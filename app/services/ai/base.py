from typing import Any, Protocol


class AIClient(Protocol):
    async def recognize_and_analyze(self, images: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]: ...

    async def analyze_attempt(self, images: list[dict[str, Any]], question: dict, task_id: int) -> dict[str, Any]: ...
