"""One executable context and atomic build batch for layouts sharing a store."""
from rlayout import factory as native

from ._context import FactoryContext
from ._source import SourceContextChanged, digest


class CacheSession:
    def __init__(self, members):
        self.members = members
        self.path = members[0].path
        self.frames = []
        self.staged = []
        layouts = tuple(member.layout for member in members)
        contexts = [FactoryContext(member.layout, member.path, member.source_roots, layouts)
                    for member in members]
        identity = digest(("kfactory-store-context-1",
                           tuple((member.layout.name, context.identity)
                                 for member, context in zip(members, contexts, strict=True))))
        values = native.InputValues()
        observed = {}
        for member in members:
            for kind, entries in (("value", member.current_values), ("version", member.current_versions)):
                for name, encoded in entries.items():
                    key = (kind, name)
                    if key in observed and observed[key] != encoded:
                        raise SourceContextChanged(f"conflicting shared {kind} input: {name}")
                    observed[key] = encoded
                    getattr(values, "set_" + kind)(name, encoded)
        current_values = {name: encoded for (kind, name), encoded in observed.items() if kind == "value"}
        current_versions = {name: encoded for (kind, name), encoded in observed.items() if kind == "version"}
        for member, context in zip(members, contexts, strict=True):
            member.current_values = current_values
            member.current_versions = current_versions
            context.identity = identity
            context.session = self
            member.context = context
            member.session = self
            member.staged = self.staged
            member.values = values

    @staticmethod
    def participants(path):
        from ..layout import kcls
        return tuple(sorted((layout.persistent_cache for layout in kcls.values()
                             if layout.persistent_cache is not None
                             and layout.persistent_cache.path == path),
                            key=lambda member: member.layout.name))

    @classmethod
    def open(cls, member):
        members = cls.participants(member.path)
        if member not in members:
            raise SourceContextChanged("persistent layout is no longer registered")
        existing = next((peer.session for peer in members if peer.session is not None), None)
        if existing is not None:
            existing.check()
            return existing
        return cls(members)

    def check(self):
        if self.participants(self.path) != self.members:
            raise SourceContextChanged("layouts sharing the store changed; restart the context")
        for member in self.members:
            member.context.check_local()

    def accept_active_outputs(self):
        for member in self.members:
            if member.frames:
                member.context.accept_output_registrations()

    def mark_untracked(self):
        for _, frame in self.frames:
            frame.persistable = False

    def check_provenance(self):
        valid = all(member._check_provenance() for member in self.members)
        if not valid:
            for member in self.members:
                member.native.forget_client()
                member.entries.clear()
                member.cells.clear()
                member.proofs.clear()
            self.mark_untracked()
        return valid

    def publish(self, authority):
        self.check()
        if not self.check_provenance() or any(
            member._fingerprint(cell) != proof
            for member in self.members for cell, proof in member.pending_proofs.values()
        ):
            from ._source import NotPersistable
            raise NotPersistable("native inputs changed during the build")
        authority.store.publish_batch(self.staged, authority.values)
        for member in self.members:
            member.store.remember_cells(member.native, [(key, index) for index, key in member.pending_cells.items()])
            member.cells.update(member.pending_cells)
            member.entries.update(member.pending_entries)
            member.proofs.update(member.pending_proofs)

    def discard_staging(self):
        self.staged.clear()
        for member in self.members:
            member.pending_entries.clear()
            member.pending_cells.clear()
            member.pending_proofs.clear()
        for member in self.members:
            member.context.accept_output_registrations()

    def reset(self):
        if self.frames:
            raise RuntimeError("cannot reset the context during a persistent factory build")
        for member in self.members:
            member.context = None
            member.session = None
            member.staged = []
