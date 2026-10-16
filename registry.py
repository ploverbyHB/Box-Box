"""
Name → implementation registry. Subpackages of a registry's package register themselves on import.
"""
import importlib
import pkgutil


class Registry:
  def __init__(self, kind: str):
    self.kind = kind
    self._items = {}

  def add(self, name: str, item):
    if name in self._items:
      raise ValueError(f"{self.kind} {name!r} is already registered")
    self._items[name] = item
    return item

  def register(self, name: str):
    return lambda item: self.add(name, item)

  def get(self, name: str):
    if name not in self._items:
      raise ValueError(f"Unknown {self.kind} {name!r}. Available: {', '.join(self.names())}")
    return self._items[name]

  def find(self, name: str, default=None):
    return self._items.get(name, default)

  def names(self) -> list[str]:
    return sorted(self._items)

  def discover(self, package_name: str, package_path) -> None:
    for module in pkgutil.iter_modules(package_path):
      if module.ispkg:
        importlib.import_module(f"{package_name}.{module.name}")
