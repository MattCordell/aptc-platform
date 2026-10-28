"""Unit tests for `audit_call_graph_support.reachable` (issue #165) against
synthetic sources, so a walker bug can be pinned down without depending on
the shape of the real routers - `test_audit_route_inventory.py` exercises
this walker against the real app separately.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


walker = _load("audit_call_graph_support")


@pytest.fixture(autouse=True)
def _fresh_module_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Every test gets its own `SRC_ROOT` and a clean `_parse_module` cache
    - without the clear, a module name reused across tests (e.g. `pkg.mod`)
    would resolve to whichever test happened to parse it first."""
    monkeypatch.setattr(walker, "SRC_ROOT", tmp_path)
    walker._parse_module.cache_clear()
    yield
    walker._parse_module.cache_clear()


def _write(tmp_path: Path, relative: str, source: str) -> None:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


TARGET = frozenset({("aptc.audit", "record_change")})


def test_reaches_target_directly(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "aptc/audit.py",
        "def record_change(*a, **k):\n    pass\n",
    )
    _write(
        tmp_path,
        "aptc/endpoint.py",
        "from aptc.audit import record_change\n\n\ndef handler():\n    record_change()\n",
    )

    assert walker.reachable("aptc.endpoint", "handler", targets=TARGET)


def test_cross_module_hop_reaches_target(tmp_path: Path) -> None:
    """The known shape of every real mutating route: endpoint -> one
    service-layer function -> `record_change`, resolved through two
    separate modules' own import tables."""
    _write(tmp_path, "aptc/audit.py", "def record_change(*a, **k):\n    pass\n")
    _write(
        tmp_path,
        "aptc/service.py",
        "from aptc.audit import record_change\n\n\ndef save(entry):\n    record_change(entry)\n",
    )
    _write(
        tmp_path,
        "aptc/endpoint.py",
        "from aptc.service import save\n\n\ndef handler(body):\n    save(body)\n",
    )

    assert walker.reachable("aptc.endpoint", "handler", targets=TARGET)


def test_same_module_helper_hop_reaches_target(tmp_path: Path) -> None:
    """A route that calls a private helper defined in its own module (no
    import needed) which in turn calls the audit function."""
    _write(tmp_path, "aptc/audit.py", "def record_change(*a, **k):\n    pass\n")
    _write(
        tmp_path,
        "aptc/endpoint.py",
        "from aptc.audit import record_change\n\n\n"
        "def _do_write():\n    record_change()\n\n\n"
        "def handler():\n    _do_write()\n",
    )

    assert walker.reachable("aptc.endpoint", "handler", targets=TARGET)


def test_import_cycle_terminates_without_reaching_target(tmp_path: Path) -> None:
    """`a` and `b` call into each other forever if the walk does not track
    visited (module, qualname) pairs; neither reaches `record_change`."""
    _write(tmp_path, "aptc/audit.py", "def record_change(*a, **k):\n    pass\n")
    _write(
        tmp_path,
        "aptc/a.py",
        "from aptc.b import b_func\n\n\ndef a_func():\n    b_func()\n",
    )
    _write(
        tmp_path,
        "aptc/b.py",
        "from aptc.a import a_func\n\n\ndef b_func():\n    a_func()\n",
    )

    assert walker.reachable("aptc.a", "a_func", targets=TARGET) is False


def test_unresolvable_attribute_call_does_not_reach_target(tmp_path: Path) -> None:
    """`session.flush()`-shaped calls (base is a parameter, not an import)
    must not be mistaken for reaching anything - the walker never guesses
    at a call it cannot tie to a known module."""
    _write(tmp_path, "aptc/audit.py", "def record_change(*a, **k):\n    pass\n")
    _write(
        tmp_path,
        "aptc/endpoint.py",
        "def handler(session):\n    session.flush()\n",
    )

    assert walker.reachable("aptc.endpoint", "handler", targets=TARGET) is False


def test_call_to_function_with_no_source_on_disk_is_a_leaf(tmp_path: Path) -> None:
    """A call to a third-party/stdlib name (nothing under `SRC_ROOT` to
    parse) must not blow up the walk - it is simply a leaf."""
    _write(
        tmp_path,
        "aptc/endpoint.py",
        "from fastapi import Response\n\n\ndef handler():\n    Response()\n",
    )

    assert walker.reachable("aptc.endpoint", "handler", targets=TARGET) is False


def test_relative_import_hop_reaches_target(tmp_path: Path) -> None:
    """`from . import audit` / `from .audit import record_change`-style
    relative imports, used throughout `aptc.catalogue.*`, resolve the same
    as an absolute import."""
    _write(tmp_path, "aptc/__init__.py", "")
    _write(tmp_path, "aptc/audit.py", "def record_change(*a, **k):\n    pass\n")
    _write(
        tmp_path,
        "aptc/endpoint.py",
        "from .audit import record_change\n\n\ndef handler():\n    record_change()\n",
    )

    assert walker.reachable("aptc.endpoint", "handler", targets=TARGET)


def test_submodule_attribute_call_reaches_target(tmp_path: Path) -> None:
    """`from aptc import audit` then `audit.record_change(...)` - the
    submodule-import shape used by e.g. `aptc/api/app.py`'s router
    imports."""
    _write(tmp_path, "aptc/__init__.py", "")
    _write(tmp_path, "aptc/audit.py", "def record_change(*a, **k):\n    pass\n")
    _write(
        tmp_path,
        "aptc/endpoint.py",
        "from aptc import audit\n\n\ndef handler():\n    audit.record_change()\n",
    )

    assert walker.reachable("aptc.endpoint", "handler", targets=TARGET)


def test_missing_function_is_not_reachable(tmp_path: Path) -> None:
    """A qualname that does not exist in the named module (a stale
    bridge from route to source) fails closed, not with an exception."""
    _write(tmp_path, "aptc/endpoint.py", "def handler():\n    pass\n")

    assert walker.reachable("aptc.endpoint", "nonexistent", targets=TARGET) is False


def test_relative_import_from_a_package_init_resolves_within_the_package(
    tmp_path: Path,
) -> None:
    """PR #270 review: the importing module here is a *package's*
    `__init__.py` (`aptc.pkg`), whose own dotted name already is the
    package a `from .` inside it counts levels from - unlike a plain
    module, where the package is the parent. Getting this wrong resolves
    `from .svc import save` in `aptc/pkg/__init__.py` to `aptc.svc`
    instead of `aptc.pkg.svc`."""
    _write(
        tmp_path, "aptc/pkg/__init__.py", "from .svc import save\n\n\ndef handler():\n    save()\n"
    )
    _write(
        tmp_path,
        "aptc/pkg/svc.py",
        "from aptc.audit import record_change\n\n\ndef save():\n    record_change()\n",
    )
    _write(tmp_path, "aptc/audit.py", "def record_change(*a, **k):\n    pass\n")

    assert walker.reachable("aptc.pkg", "handler", targets=TARGET)


def test_dotted_import_binds_only_the_top_level_name(tmp_path: Path) -> None:
    """PR #270 review: `import aptc.audit.recording` binds only the local
    name `aptc` (to the top package) - never the dotted path itself - so a
    call written as `aptc.audit.recording.record_change(...)` has to be
    resolved by folding the whole attribute chain back into a module path,
    not by treating the import statement's own dotted name as what got
    bound locally."""
    _write(tmp_path, "aptc/audit/__init__.py", "")
    _write(tmp_path, "aptc/audit/recording.py", "def record_change(*a, **k):\n    pass\n")
    _write(
        tmp_path,
        "aptc/endpoint.py",
        "import aptc.audit.recording\n\n\n"
        "def handler():\n    aptc.audit.recording.record_change()\n",
    )

    assert walker.reachable(
        "aptc.endpoint", "handler", targets=frozenset({("aptc.audit.recording", "record_change")})
    )


def test_function_scoped_import_is_visible_to_the_walker(tmp_path: Path) -> None:
    """PR #270 review: an import inside the function body (a real shape
    used in this repo, e.g. `aptc/catalogue/designations.py`) must not be
    invisible just because the walker's import table was only ever built
    from top-level statements."""
    _write(tmp_path, "aptc/audit.py", "def record_change(*a, **k):\n    pass\n")
    _write(
        tmp_path,
        "aptc/endpoint.py",
        "def handler():\n    from aptc.audit import record_change\n\n    record_change()\n",
    )

    assert walker.reachable("aptc.endpoint", "handler", targets=TARGET)


def test_function_scoped_import_in_a_sibling_function_does_not_shadow(tmp_path: Path) -> None:
    """PR #270 round 2 review: collecting imports from a whole-module
    `ast.walk` gave one flat, name-keyed table, so a function-scoped
    import in one function overwrote a module-level (or another
    function's local) binding of the same name *everywhere* - including
    in a sibling function that never sees that import at all. Confirmed
    to produce a false `reachable() is True` before the fix (an
    unrelated `helper` shadowed by `other`'s local `record_change as
    helper`, while `handler` calls the real, unrelated `helper` and never
    reaches `record_change`)."""
    _write(tmp_path, "aptc/audit.py", "def record_change(*a, **k):\n    pass\n")
    _write(tmp_path, "aptc/helpers.py", "def helper():\n    pass\n")
    _write(
        tmp_path,
        "aptc/shadow.py",
        "from aptc.helpers import helper\n\n\n"
        "def other():\n"
        "    from aptc.audit import record_change as helper\n\n"
        "    helper()\n\n\n"
        "def handler():\n"
        "    helper()\n",
    )

    assert walker.reachable("aptc.shadow", "handler", targets=TARGET) is False


def test_async_endpoint_reaches_target(tmp_path: Path) -> None:
    """Every real route handler is `async def` - `_find_function`'s
    `AsyncFunctionDef` branch needs its own direct test, not only indirect
    coverage through the real app."""
    _write(tmp_path, "aptc/audit.py", "def record_change(*a, **k):\n    pass\n")
    _write(
        tmp_path,
        "aptc/endpoint.py",
        "from aptc.audit import record_change\n\n\nasync def handler():\n    record_change()\n",
    )

    assert walker.reachable("aptc.endpoint", "handler", targets=TARGET)


def test_class_method_qualname_reaches_target(tmp_path: Path) -> None:
    """`_find_function` explicitly supports a dotted `Class.method`
    qualname; nothing else in this file exercised it."""
    _write(tmp_path, "aptc/audit.py", "def record_change(*a, **k):\n    pass\n")
    _write(
        tmp_path,
        "aptc/endpoint.py",
        "from aptc.audit import record_change\n\n\n"
        "class Handler:\n    def method(self):\n        record_change()\n",
    )

    assert walker.reachable("aptc.endpoint", "Handler.method", targets=TARGET)


def test_is_resolvable_true_for_a_real_function(tmp_path: Path) -> None:
    _write(tmp_path, "aptc/endpoint.py", "def handler():\n    pass\n")

    assert walker.is_resolvable("aptc.endpoint", "handler")


def test_is_resolvable_false_for_a_module_with_no_source_on_disk(tmp_path: Path) -> None:
    assert walker.is_resolvable("aptc.nonexistent", "handler") is False


def test_is_resolvable_false_for_a_function_missing_from_a_real_module(tmp_path: Path) -> None:
    _write(tmp_path, "aptc/endpoint.py", "def handler():\n    pass\n")

    assert walker.is_resolvable("aptc.endpoint", "nonexistent") is False
