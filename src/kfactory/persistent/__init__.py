"""Optional persistent factory authority backed by RLayout's Rust store.

Enable with ``KCLayout(..., cache_path=...)``. Source/configuration is stable for
one process: a watched edit requires a clean restart. Unsupported recipes run
uncached and expose a diagnostic; arbitrary I/O needs explicit declarations.
"""
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
from pathlib import Path

from rlayout import factory as native

from .. import kdb
from ..cross_section import SymmetricalCrossSection, AsymmetricalCrossSection
from ..enclosure import LayerEnclosure
from ._session import CacheSession
from ._source import NotPersistable, SourceContextChanged, canonical


@dataclass
class Frame:
    build: object
    before: set
    persistable: bool = True
    names: dict = field(default_factory=dict)
    allocations: int = 0


@dataclass
class Entry:
    cell: object
    result: bytes
    producer: str
    call: bytes


class FactoryCacheView(Mapping):
    """Inspection and durable invalidation; never a second hit authority."""
    def __init__(self, authority, factory):
        self.authority, self.factory = authority, factory

    def __iter__(self):
        return iter(self.authority.inspect(self.factory))

    def __len__(self):
        return len(self.authority.inspect(self.factory))

    def __getitem__(self, key):
        return self.authority.inspect(self.factory)[key]

    def clear(self):
        self.authority.invalidate(self.factory.qualified_name)

    def __delitem__(self, key):
        # Removing a memo entry means recomputation, including in other processes.
        self.clear()


class PersistentCache:
    def __init__(self, layout, path):
        self.layout = layout
        self.path = Path(path).resolve()
        self.store = native.Store(self.path)
        self.native = native.LayoutCache(layout.layout)
        self.context = None
        self.session = None
        self.current_values = {}
        self.current_versions = {}
        self.source_roots = set()
        self.values = native.InputValues()
        self.frames = []
        self.staged = []
        self.pending_entries = {}
        self.pending_cells = {}
        self.pending_proofs = {}
        self.entries = {}
        self.cells = {}
        self.proofs = {}
        self.diagnostics = []
        self.fallback_depth = 0
        self.generic_depth = 0

    def watch_source_root(self, path):
        """Watch an additional source directory/file, before the first call.

        Directories use the same Python-source manifest rules as discovered roots.
        Use declare_file for runtime data, including non-Python configuration.
        """
        if self.context is not None:
            raise SourceContextChanged("source roots must be configured before the first factory call")
        root = Path(path).resolve(strict=True)
        if root in (Path(root.anchor), Path.home()):
            raise ValueError("source root must be a bounded project directory or file")
        if not (root.is_dir() or root.is_file()):
            raise ValueError("source root must be a regular file or directory")
        self.source_roots.add(root)

    def record_allocation(self, cell, requested):
        if self.frames and not self.fallback_depth:
            frame = self.frames[-1]
            # Only constructor-known anonymous allocations are normalized. Never
            # infer intent from a name pattern or rename the live native cell.
            intended = requested if requested is not None else f"Unnamed_{frame.allocations}"
            frame.allocations += 1
            frame.names[cell.index] = (intended, cell.name)

    def record_proxy_import(self, previous_indices):
        if not self.frames:
            return
        for item in self.layout.layout.each_cell():
            if item.index in previous_indices or not item.is_library_cell():
                continue
            peer = next((member for member in self.session.members
                         if member.layout.layout.library().id == item.library().id), None)
            if peer is None:
                continue
            source_index = item.library_cell_index()
            key = peer.pending_cells.get(source_index, peer.cells.get(source_index))
            if key is None:
                continue
            pending = next((prepared for prepared in self.staged if prepared.id == key[0]), None)
            intended = pending.cell_name(key[1]) if pending is not None else self.store.cell_name(key)
            self.frames[-1].names[item.index] = (intended, item.name)

    def record_name(self, cell, requested, actual):
        if self.frames:
            self.frames[-1].names[cell.cell_index()] = (requested, actual)

    def view(self, factory):
        return FactoryCacheView(self, factory)

    def _context(self):
        if self.session is None:
            CacheSession.open(self)
        self.session.accept_active_outputs()
        self.session.check()
        return bytes.fromhex(self.context.identity)

    @contextmanager
    def generic_scope(self):
        """Track definitions generated while normalizing a generic factory call."""
        try:
            self._context()
        except NotPersistable:
            # The delegated cell call provides the normal uncached diagnostic.
            yield
            return
        self.generic_depth += 1
        try:
            yield
        finally:
            self.generic_depth -= 1
            self.context.accept_output_registrations()
            self.context.check()

    def _diagnose(self, factory, reason):
        item = (factory.qualified_name, str(reason))
        if item not in self.diagnostics:
            self.diagnostics.append(item)

    def _sync_metadata(self, cell):
        # KFactory's writer clears metadata before writing its own records.
        # Preserve unrelated native metadata across that existing conversion.
        other = [m for m in cell.kdb_cell.meta_info() if not m.name.startswith("kfactory:")]
        cell.set_meta_data(local=True)
        for metadata in other:
            cell.kdb_cell.add_meta_info(metadata)
        definitions = {}
        for port in cell.ports:
            xs = port.base.any_cross_section
            definitions[xs.name] = ("symmetric" if isinstance(xs, SymmetricalCrossSection) else "asymmetric", xs.model_dump())
        if definitions:
            cell.kdb_cell.add_meta_info(kdb.LayoutMetaInfo("kfactory:cache:cross_sections", definitions, "", True))

    def _restore_port_definitions(self, cell):
        for metadata in cell.kdb_cell.meta_info():
            if metadata.name != "kfactory:cache:cross_sections":
                continue
            for kind, record in metadata.value.values():
                data = dict(record)
                if kind == "symmetric":
                    data["enclosure"] = LayerEnclosure(**data["enclosure"])
                    self.layout.get_symmetrical_cross_section(SymmetricalCrossSection(**data))
                elif kind == "asymmetric":
                    self.layout.get_asymmetrical_cross_section(AsymmetricalCrossSection(**data))
                else:
                    raise ValueError("unsupported cached port-definition kind")

    def _fingerprint(self, cell):
        if cell.destroyed():
            raise NotPersistable("deleted cell has no current native provenance")
        self._sync_metadata(cell)
        return self.native.cell_fingerprint(cell.kdb_cell)

    def _check_provenance(self):
        for cell, expected in self.proofs.values():
            try:
                current = self._fingerprint(cell)
            except (NotPersistable, ValueError):
                current = None
            if current != expected:
                self.native.forget_client()
                self.cells.clear()
                self.proofs.clear()
                self.entries.clear()
                self.session.mark_untracked()
                return False
        return True

    def inspect(self, factory):
        context = self._context()
        self.session.check_provenance()
        return {call: entry.cell for (producer, call), entry in self.entries.items()
                if producer == factory.qualified_name
                and self.store.invalid_reason(entry.result, context, self.values) is None}

    def invalidate(self, producer):
        self.store.invalidate(self.layout.name, producer)
        self.entries = {key: value for key, value in self.entries.items() if key[0] != producer}

    def invalidate_all(self, *, reset_context=False):
        if reset_context and self.session is not None and self.session.frames:
            raise RuntimeError("cannot clear the layout during a persistent factory build")
        if reset_context and self.context is not None:
            self.context.check()
        for factory in self.layout.factories.all():
            self.invalidate(factory.qualified_name)
        self.native.forget_client()
        self.entries.clear()
        self.cells.clear()
        self.proofs.clear()
        for frame in self.frames:
            frame.persistable = False
        if reset_context and self.session is not None:
            self.session.reset()

    def _result_key(self, cell, context):
        from ..kcell import ProtoTKCell
        if not isinstance(cell, ProtoTKCell):
            raise NotPersistable("prebuilt input needs a tracked KCell or DKCell")
        if cell.kcl is not self.layout:
            peer = cell.kcl.persistent_cache
            if peer is None or peer.session is not self.session:
                raise NotPersistable("prebuilt input needs a layout sharing this store context")
            return peer._result_key(cell, context)
        index = cell.cell_index()
        key = self.pending_cells.get(index, self.cells.get(index))
        proof = self.pending_proofs.get(index, self.proofs.get(index))
        if key is None or proof is None or self._fingerprint(cell) != proof[1]:
            raise NotPersistable("prebuilt input has no unchanged persistent provenance")
        if index not in self.pending_cells and self.store.invalid_reason(key[0], context, self.values) is not None:
            raise NotPersistable("prebuilt input result is invalid; request its factory again")
        return key

    def _encode_call(self, params, context):
        from ..kcell import ProtoTKCell
        dependencies = set()
        def adapt(value):
            if isinstance(value, ProtoTKCell):
                key = self._result_key(value, context)
                dependencies.add(key[0])
                return ("persistent-cell", self.context.encode(type(value)), self.store.library_id, key)
            return self.context.adapt(value)
        return canonical(params, adapt=adapt), dependencies

    def declare_result(self, cell):
        """Track a prebuilt cell read during this build, even without instances.

        Untracked or invalid input cells make the active build non-persistable;
        requesting their factory again establishes a current result version.
        """
        if self.fallback_depth:
            return
        frame = self._frame()
        self.session.check_provenance()
        try:
            key = self._result_key(cell, bytes.fromhex(self.context.identity))
        except NotPersistable as reason:
            frame.persistable = False
            diagnostic = ("declare_result", str(reason))
            if diagnostic not in self.diagnostics:
                self.diagnostics.append(diagnostic)
        else:
            frame.build.observe_result(key[0])

    def declare_file(self, path):
        if self.fallback_depth:
            return
        self._frame().build.observe_file(path)

    def declare_environment(self, name):
        if self.fallback_depth:
            return
        self._frame().build.observe_environment(name)

    def set_value(self, name, value):
        encoded = canonical(value)
        self.current_values[name] = encoded
        self.values.set_value(name, encoded)

    def set_version(self, name, value):
        encoded = canonical(value)
        self.current_versions[name] = encoded
        self.values.set_version(name, encoded)

    def declare_value(self, name, value):
        encoded = canonical(value)
        self.current_values[name] = encoded
        self.values.set_value(name, encoded)
        if not self.fallback_depth:
            self._frame().build.observe_value(name, encoded)

    def declare_version(self, name, value):
        encoded = canonical(value)
        self.current_versions[name] = encoded
        self.values.set_version(name, encoded)
        if not self.fallback_depth:
            self._frame().build.observe_version(name, encoded)

    def _frame(self):
        if not self.frames:
            raise RuntimeError("dependency declarations require an active persistent factory call")
        return self.frames[-1]

    def _record(self, entry):
        if self.session is not None and self.session.frames:
            frame = self.session.frames[-1][1]
            if entry is None:
                frame.persistable = False
            else:
                frame.build.observe_result(entry.result)

    def _map_proxy(self, frame, item, context):
        library = item.library()
        peer = next((member for member in self.session.members
                     if member.layout.layout.library().id == library.id), None)
        if peer is None:
            raise NotPersistable("proxy source is outside this store context")
        source = peer.layout.get_cell(item.library_cell_index())
        key = peer._result_key(source, context)
        if not self.native.proxy_matches_source(item, source.kdb_cell):
            raise NotPersistable("proxy geometry differs from its tracked native source")
        frame.build.map_library_cell(peer.layout.layout, source.cell_index(), key, peer.layout.name)

    def _load(self, factory, result, context, call):
        members = (self, *(member for member in self.session.members if member is not self))
        targets = [(member.layout.name, member.native) for member in members]
        root = self.store.materialize_into_libraries(targets, result, context, self.values)
        # Combined geometry order restores real sources before their proxies.
        for key in self.store.geometry_closure(self.store.root(result)):
            for member in members:
                native_cell = member.native.cell(key)
                if native_cell is None:
                    continue
                if native_cell.index not in member.cells:
                    locked = native_cell.locked
                    try:
                        # Wrapping a native proxy already restores source ports.
                        # Unlock before creating its wrapper, and restore even
                        # when wrapper construction or metadata decoding fails.
                        native_cell.locked = False
                        cell = member.layout.get_cell(native_cell.index)
                        member._restore_port_definitions(cell)
                        cell.get_meta_data(local=True)
                    finally:
                        native_cell.locked = locked
                else:
                    cell = member.layout.get_cell(native_cell.index)
                member.cells[native_cell.index] = key
                member.proofs[native_cell.index] = (cell, member._fingerprint(cell))
        for member in members:
            member.context.accept_output_registrations()
        self.context.check()
        cell = self.layout.get_cell(root.index, factory.output_type)
        return Entry(cell, result, factory.qualified_name, call)

    def call(self, factory, params, execute):
        if self.fallback_depth:
            return execute()
        try:
            context = self._context()
            outer = not self.session.frames
            if not factory.persistent or factory.qualified_name not in self.context.factory_names:
                raise NotPersistable("factory opts out or is not registered in the context")
            if factory.qualified_name in self.context.unsupported:
                raise NotPersistable(self.context.unsupported[factory.qualified_name])
            self.session.check_provenance()
            for member in self.session.members:
                for pending_cell, expected in member.pending_proofs.values():
                    if member._fingerprint(pending_cell) != expected:
                        self.session.mark_untracked()
                        member.pending_entries.clear()
                        break
            encoded, input_results = self._encode_call(params, context)
            call = hashlib.sha256(encoded).digest()
        except NotPersistable as reason:
            self._diagnose(factory, reason)
            self._record(None)
            self.fallback_depth += 1
            try:
                return execute()
            finally:
                self.fallback_depth -= 1
        key = (factory.qualified_name, call)
        pending = self.pending_entries.get(key)
        if pending is not None:
            self.context.check()
            self._record(pending)
            return pending.cell
        current = self.entries.get(key)
        if current is not None and self.store.invalid_reason(current.result, context, self.values) is None:
            self.context.check()
            self._record(current)
            return current.cell
        for result in self.store.variants(self.layout.name, factory.qualified_name, call):
            if self.store.invalid_reason(result, context, self.values) is None:
                entry = self._load(factory, result, context, call)
                self.entries[key] = entry
                self._record(entry)
                return entry.cell
        frame = Frame(self.store.begin_build(self.layout.name, factory.qualified_name, call, context),
                      {cell.index for cell in self.layout.layout.each_cell()})
        for result in input_results:
            frame.build.observe_result(result)
        self.frames.append(frame)
        self.session.frames.append((self, frame))
        entry = None
        try:
            cell = execute()
            self.context.accept_output_registrations()
            self.context.check()
            try:
                if not frame.persistable:
                    raise NotPersistable("a nested call has untracked inputs")
                if cell.kcl is not self.layout:
                    raise NotPersistable("result belongs to another KCLayout")
                owned, stored, pending = [], [], [cell.kdb_cell]
                seen = set()
                while pending:
                    item = pending.pop()
                    if item.index in seen:
                        continue
                    seen.add(item.index)
                    existing = self.pending_cells.get(item.index, self.cells.get(item.index))
                    if existing is not None and item.index != cell.cell_index():
                        stored.append((item.index, existing))
                        continue
                    if item.index in frame.before:
                        raise NotPersistable("untracked preexisting native cell in result hierarchy")
                    if item.is_library_cell():
                        self._map_proxy(frame, item, context)
                    owned.append(item.index)
                    wrapped = self.layout.get_cell(item.index)
                    self._sync_metadata(wrapped)
                    item.locked = True
                    pending.extend(item.child_cells())
                prepared = self.store.prepare(frame.build, self.layout.layout, cell.cell_index(), owned,
                                              stored=stored, staged=self.staged)
                for ordinal, index in enumerate(owned):
                    intended = frame.names.get(index)
                    if intended is not None and self.layout.layout.cell(index).name == intended[1]:
                        prepared.set_cell_name(ordinal, intended[0])
                for index in owned:
                    wrapped = self.layout.get_cell(index)
                    self.pending_proofs[index] = (wrapped, self._fingerprint(wrapped))
                self.staged.append(prepared)
                self.pending_cells.update({index: (prepared.id, ordinal) for ordinal, index in enumerate(owned)})
                entry = Entry(cell, prepared.id, factory.qualified_name, call)
                self.pending_entries[key] = entry
                if outer:
                    self.session.publish(self)
            except NotPersistable as reason:
                entry = None
                self._diagnose(factory, reason)
            return cell
        finally:
            self.frames.pop()
            self.session.frames.pop()
            self._record(entry)
            if outer:
                self.session.discard_staging()
