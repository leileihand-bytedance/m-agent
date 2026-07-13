from collections.abc import Callable


class ToolNotAllowedError(RuntimeError):
    pass


class ToolGateway:
    def __init__(self, allowed_tools: tuple[str, ...], tools: dict[str, Callable[..., object]]):
        self._allowed_tools = set(allowed_tools)
        self._tools = tools

    def call(self, tool_name: str, *args: object, **kwargs: object) -> object:
        if tool_name not in self._allowed_tools:
            raise ToolNotAllowedError(f"Tool is not allowed for this skill: {tool_name}")
        if tool_name not in self._tools:
            raise KeyError(f"Tool is not registered: {tool_name}")
        return self._tools[tool_name](*args, **kwargs)
