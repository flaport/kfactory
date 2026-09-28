"""Canonical Python recipes and conservative source manifests.

Source-backed functions and supported captures are inspected; arbitrary I/O,
hot reload, import-time side effects and concurrent source ABA are not inferred.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache, partial
import ast
import dis
import hashlib
import inspect
import json
import marshal
import math
import os
from pathlib import Path
import sys
import sysconfig
from types import CodeType, FunctionType, ModuleType


class NotPersistable(ValueError):
    """The adapter cannot represent or verify an input without a declaration."""


class SourceContextChanged(RuntimeError):
    """Tracked sources or executable/configuration state changed; restart."""


def canonical(value, *, adapt=None):
    """Versioned, typed bytes: no Python hash, repr, pickle or object address."""
    active = set()

    def tree(item):
        kind = type(item)
        if item is None:
            return ["null"]
        if kind is bool:
            return ["bool", item]
        if kind is int:
            return ["int", str(item)]
        if kind is float:
            if not math.isfinite(item):
                raise NotPersistable("non-finite floating value")
            return ["float", item.hex()]
        if kind is str:
            return ["str", item]
        if kind is bytes:
            return ["bytes", item.hex()]
        if kind not in (tuple, list, dict, set, frozenset):
            if adapt is not None:
                if id(item) in active:
                    raise NotPersistable("cyclic adapted input value")
                active.add(id(item))
                try:
                    description = adapt(item)
                    if description is not NotImplemented:
                        # Keep native/config values distinct from ordinary tuples.
                        return ["adapted", tree(description)]
                finally:
                    active.remove(id(item))
            raise NotPersistable(f"unsupported value type: {kind.__module__}.{kind.__qualname__}")
        if id(item) in active:
            raise NotPersistable("cyclic input value")
        active.add(id(item))
        try:
            if kind is dict:
                entries = [[tree(key), tree(value)] for key, value in item.items()]
                entries.sort(key=lambda pair: json.dumps(pair[0], ensure_ascii=True, separators=(",", ":")))
                return ["dict", entries]
            entries = [tree(value) for value in item]
            if kind in (set, frozenset):
                entries.sort(key=lambda value: json.dumps(value, ensure_ascii=True, separators=(",", ":")))
            return [kind.__name__, entries]
        finally:
            active.remove(id(item))

    return json.dumps(["kfactory-persistent-call-1", tree(value)], ensure_ascii=True, separators=(",", ":")).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def normalized_call(function, *args, **kwargs):
    bound = inspect.signature(function).bind(*args, **kwargs)
    bound.apply_defaults()
    return canonical(dict(bound.arguments))


EXCLUDED = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "__pycache__", ".tox",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".cache", "node_modules",
    "build", "dist", "target",
})


def discover_root(filename):
    path = Path(filename).resolve()
    if not path.is_file() or path.suffix != ".py":
        raise NotPersistable("callable needs readable Python source or an explicit producer version")
    # Installed packages inside a project's .venv must not inherit that project
    # as their root (the project's manifest deliberately excludes .venv).
    for parent in path.parents:
        if parent.name in ("site-packages", "dist-packages"):
            relative = path.relative_to(parent)
            return parent / relative.parts[0] if len(relative.parts) > 1 else path
        if (parent / "pyproject.toml").is_file() or (parent / ".git").exists():
            if parent == Path(parent.anchor) or parent == Path.home():
                raise NotPersistable("refusing an unbounded source root")
            return parent
    root = path.parent
    while (root / "__init__.py").is_file() and (root.parent / "__init__.py").is_file():
        root = root.parent
    if root == Path(root.anchor) or root == Path.home():
        raise NotPersistable("refusing an unbounded source root")
    return root


@dataclass(frozen=True)
class Manifest:
    entries: tuple[tuple[str, str, str], ...]
    source_bytes: int

    @property
    def identity(self):
        return digest(self.entries)


def manifest(roots):
    entries = []
    size = 0
    for root in sorted(set(roots)):
        if root.is_file():
            content = root.read_bytes()
            size += len(content)
            entries.append((str(root), root.name, hashlib.sha256(content).hexdigest()))
            continue
        for directory, dirs, filenames in os.walk(root, followlinks=False):
            dirs[:] = sorted(name for name in dirs if name not in EXCLUDED)
            if any((Path(directory) / name).is_symlink() for name in dirs):
                raise NotPersistable("symlinked source directory needs an explicit root policy")
            for name in sorted(filenames):
                if not name.endswith((".py", ".pyi")) and name not in ("pyproject.toml", "setup.cfg"):
                    continue
                path = Path(directory) / name
                if path.is_symlink():
                    raise NotPersistable("symlinked source file needs an explicit root policy")
                content = path.read_bytes()
                size += len(content)
                entries.append((str(root), str(path.relative_to(root)), hashlib.sha256(content).hexdigest()))
    return Manifest(tuple(entries), size)


def code_bytes(code):
    constants = tuple(code_bytes(value) if isinstance(value, CodeType) else value for value in code.co_consts)
    normalized = code.replace(co_filename="", co_firstlineno=1, co_linetable=b"", co_consts=constants)
    return marshal.dumps(normalized)


def codes(code):
    yield code
    for constant in code.co_consts:
        if isinstance(constant, CodeType):
            yield from codes(constant)


@lru_cache(maxsize=32)
def compiled_source(filename, content, optimization):
    """Reuse parsing only for identical bytes; callers still read every boundary.

    This caches no factory result, manifest, recipe or validity decision.
    Loaded bytecode, closure values and globals are checked on every call.
    """
    return compile(content, filename, "exec", dont_inherit=True, optimize=optimization)


def literal_binding(function, name, value, *, module=None):
    """Detect a stale direct module constant even if function bytecode matches."""
    module = module or sys.modules.get(function.__module__)
    filename = getattr(module, "__file__", None)
    if not filename:
        return
    for statement in ast.parse(Path(filename).read_bytes()).body:
        if isinstance(statement, ast.Assign):
            targets = statement.targets
        elif isinstance(statement, ast.AnnAssign):
            targets = [statement.target]
        else:
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            try:
                declared = ast.literal_eval(statement.value)
            except (ValueError, TypeError):
                raise NotPersistable(f"computed module global {name!r} needs a declared input") from None
            if canonical(declared) != canonical(value):
                raise SourceContextChanged(f"loaded module global {name!r} differs from its source")


def recipe(function, *, adapt=None):
    if isinstance(function, partial):
        description, roots = recipe(function.func, adapt=adapt)
        return ("partial", description, canonical(function.args, adapt=adapt),
                canonical(function.keywords, adapt=adapt)), roots
    roots = set()
    active = set()
    stdlib = Path(sysconfig.get_path("stdlib")).resolve()

    def value(item):
        if adapt is not None:
            description = adapt(item)
            if description is not NotImplemented:
                return ("adapted", canonical(description))
        if isinstance(item, FunctionType):
            return ("callable", describe(item))
        # The value tag also prevents an ordinary tuple from impersonating a
        # callable description in a closure/default.
        return ("value", canonical(item, adapt=adapt))

    def module_access(fn, name, module, instructions, index):
        if adapt is not None:
            description = adapt(module)
            if description is not NotImplemented:
                return ("adapted", canonical(description))
        source = getattr(module, "__file__", None)
        if source and Path(source).resolve().is_relative_to(stdlib) and not {"site-packages", "dist-packages"}.intersection(Path(source).parts):
            return ("stdlib", module.__name__)
        if not source:
            raise NotPersistable("module needs source or a built-in environment identity")
        roots.add(discover_root(source))
        current = module
        attributes = []
        for instruction in instructions[index + 1:]:
            if instruction.opname != "LOAD_ATTR":
                break
            if not isinstance(current, ModuleType):
                break
            attribute = instruction.argval
            if attribute not in vars(current):
                raise NotPersistable("dynamic module attributes need an explicit input boundary")
            owner = current
            current = vars(current)[attribute]
            attributes.append(attribute)
            if isinstance(current, ModuleType):
                filename = getattr(current, "__file__", None)
                if filename:
                    roots.add(discover_root(filename))
            elif not isinstance(current, FunctionType):
                literal_binding(fn, attribute, current, module=owner)
        if not attributes or isinstance(current, ModuleType):
            raise NotPersistable(f"module {name!r} must be used through a static attribute")
        return ("module-attribute", module.__name__, tuple(attributes), value(current))

    def describe(fn):
        if not isinstance(fn, FunctionType):
            raise NotPersistable("callable needs a supported source recipe or an explicit version")
        if id(fn) in active:
            raise NotPersistable("recursive callable captures need an explicit recipe boundary")
        active.add(id(fn))
        try:
            path = Path(fn.__code__.co_filename).resolve()
            root = discover_root(path)
            roots.add(root)
            compiled = compiled_source(str(path), path.read_bytes(), sys.flags.optimize)
            matches = [code for code in codes(compiled) if code.co_qualname == fn.__code__.co_qualname]
            if not any(code_bytes(code) == code_bytes(fn.__code__) for code in matches):
                raise SourceContextChanged(f"loaded callable differs from source: {fn.__qualname__}")
            globals_used = {}
            for code in codes(fn.__code__):
                instructions = list(dis.get_instructions(code))
                for index, instruction in enumerate(instructions):
                    name = instruction.argval
                    if instruction.opname in {"IMPORT_NAME", "IMPORT_FROM"}:
                        raise NotPersistable("imports inside a recipe need an explicit input boundary")
                    if instruction.opname in {"LOAD_GLOBAL", "LOAD_ATTR"} and name in {"globals", "locals", "eval", "exec", "getattr", "__import__", "__dict__"}:
                        raise NotPersistable("dynamic namespace access needs an explicit input boundary")
                    if instruction.opname != "LOAD_GLOBAL" or name not in fn.__globals__:
                        continue
                    binding = fn.__globals__[name]
                    if isinstance(binding, ModuleType):
                        description = module_access(fn, name, binding, instructions, index)
                        key = (code.co_qualname, instruction.offset, name)
                        globals_used[key] = description
                    else:
                        adapted = adapt(binding) if adapt is not None else NotImplemented
                        if not isinstance(binding, FunctionType) and adapted is NotImplemented:
                            literal_binding(fn, name, binding)
                        globals_used[name] = value(binding)
            closure = tuple(value(cell.cell_contents) for cell in (fn.__closure__ or ()))
            return (
                "function", fn.__module__, fn.__qualname__, str(path),
                hashlib.sha256(code_bytes(fn.__code__)).hexdigest(),
                tuple(value(item) for item in (fn.__defaults__ or ())),
                {name: value(item) for name, item in (fn.__kwdefaults__ or {}).items()},
                closure, globals_used,
            )
        finally:
            active.remove(id(fn))

    description = describe(function)
    return description, roots

