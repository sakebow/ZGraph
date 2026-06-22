from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, Iterable, TypeVar


T = TypeVar("T")


@dataclass(slots=True)
class Registry(Generic[T]):

    """通用注册表，用于按名称存储和检索指定类型的对象。
        类型参数:
            T: 注册表中存储的对象类型。
    """
    name: str
    _items: dict[str, T] = field(default_factory=dict)

    def register(self, key: str, item: T, *, replace: bool = False) -> None:
        """将指定键与对象注册到注册表中。
            参数:
                key: 注册键名，会被去除首尾空白并转为小写（str）。
                item: 要注册的对象（T）。
                replace: 当键已存在时是否允许替换，默认为 False。
            异常:
                ValueError: 当键名为空时抛出。
                KeyError: 当键已存在且 replace 为 False 时抛出。
            """
        normalized = key.strip().lower()
        if not normalized:
            raise ValueError("Registry key cannot be empty")
        if normalized in self._items and not replace:
            raise KeyError(f"{self.name} already contains {key!r}")
        self._items[normalized] = item

    def get(self, key: str) -> T | None:
        """根据键名获取注册表中的对象"""
        return self._items.get(key.strip().lower())

    def require(self, key: str) -> T:
        """根据键名获取注册表中的对象，键不存在时抛出异常"""
        item = self.get(key)
        if item is None:
            raise KeyError(f"{self.name} does not contain {key!r}")
        return item

    def values(self) -> list[T]:
        """返回注册表中所有已注册的对象"""
        return list(self._items.values())

    def keys(self) -> list[str]:
        """返回注册表中所有已注册的键名"""
        return list(self._items.keys())

    def items(self) -> Iterable[tuple[str, T]]:
        """返回注册表中所有的键值对"""
        return self._items.items()
