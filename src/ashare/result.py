"""轻量 Result 类型：可恢复错误用值表达，异常只留给"代码 bug"。

核心逻辑（解析、阶段执行）返回 Result，调用方据此决定流转，
而不是用 try/except 兜住一切。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar, Union

T = TypeVar("T")
E = TypeVar("E")


@dataclass(frozen=True)
class Ok(Generic[T]):
    value: T

    def is_ok(self) -> bool:
        return True


@dataclass(frozen=True)
class Err(Generic[E]):
    error: E

    def is_ok(self) -> bool:
        return False


# 一个 Result 要么是 Ok（带值），要么是 Err（带错误信息）
Result = Union[Ok[T], Err[E]]
