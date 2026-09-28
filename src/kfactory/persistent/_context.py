"""Default code/configuration context; no application-specific provider."""

from enum import Enum
from functools import partial, wraps
import hashlib
import importlib.metadata as metadata
import inspect
import json
import operator
from pathlib import Path
import sys
import sysconfig
from types import FunctionType, ModuleType, UnionType
from typing import Annotated, TypeAliasType
from typing import get_args, get_origin

import kfactory as kf
from pydantic import BaseModel
import rlayout
import rlayout._native as native

from ._source import (
    NotPersistable, SourceContextChanged, canonical, code_bytes, codes,
    compiled_source, digest, discover_root, manifest, recipe,
)


SDK_PACKAGES = {"kfactory", "rlayout"}
CONFIG_RUNTIME = {"console", "logfilter", "display_type", "show_function"}
LAYOUT_RUNTIME = {
    "layout", "library", "tkcells", "thread_lock", "factories", "virtual_factories",
    "decorators", "cross_sections", "generic_factories", "rename_function",
    "routing_strategies",
}


def qualified(value):
    return (value.__module__, value.__qualname__)


def closure_values(function):
    """Read current captures without disassembling unused global references."""
    return dict(zip(function.__code__.co_freevars,
                    (cell.cell_contents for cell in function.__closure__ or ()), strict=True))


def environment_identity():
    """Stable-installation identity, including content of the native image.

    Source packages are separately content-manifested. RECORD/direct_url capture
    distribution artifacts/editable locations; this is not an installation lock.
    """
    packages = sorted(
        (dist.metadata["Name"], dist.version,
         hashlib.sha256((dist.read_text("RECORD") or "").encode()).hexdigest(),
         dist.read_text("direct_url.json"))
        for dist in metadata.distributions() if dist.metadata["Name"]
    )
    with open(native.__file__, "rb") as stream:
        native_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    return (sys.version, sys.byteorder, rlayout.__klayout_version__, native_hash, packages)


class FactoryContext:
    """Fingerprint registered factories and their stable executable context.

    Generated cross sections/enclosures are tracked as outputs. Their later edits,
    changed configuration and changed factory registration require a new context.
    """

    def __init__(self, layout, cache_path, source_roots=(), peer_layouts=()):
        self.layout = layout
        self.peer_layouts = peer_layouts
        self.session = None
        self.cache_path = Path(cache_path).resolve()
        self.factories = layout.factories.all()
        if len({factory.qualified_name for factory in self.factories}) != len(self.factories):
            raise NotPersistable("ambiguous factory qualified names need distinct recipe registrations")
        self.originals = {factory.qualified_name: factory._f for factory in self.factories}
        self.factory_names = {factory.qualified_name for factory in self.factories}
        self.registered_factory_names = self.factory_names | {
            factory.qualified_name for peer in peer_layouts for factory in peer.factories.all()
        }
        self.package_roots = {discover_root(kf.__file__), Path(rlayout.__file__).resolve().parent}
        self.environment = environment_identity()
        self.site_roots = {Path(sysconfig.get_path(name)).resolve() for name in ("purelib", "platlib")}
        self.immutable_modules = {
            name for name, distributions in metadata.packages_distributions().items()
            if distributions and all(
                not json.loads(metadata.distribution(dist).read_text("direct_url.json") or "{}").get("dir_info", {}).get("editable", False)
                for dist in distributions
            )
        }
        self.roots = set(self.package_roots) | set(source_roots)
        self.active_functions = set()
        self.active_models = set()
        self.output_layers = {}
        self.known_layers = self.layer_table()
        self.output_cross_sections = {}
        self.output_enclosures = {}
        self.known_cross_sections = {name: self.encode(value) for name, value in layout.cross_sections.cross_sections.items()}
        self.known_enclosures = {name: self.encode(value) for name, value in layout.layer_enclosures.root.items()}
        self.description = self.describe()
        self.baseline = manifest(self.roots)
        self.identity = digest(("kfactory-persistent-context-1", self.environment, self.description, self.baseline.entries))

    def adapt(self, value):
        if value is inspect.Parameter.empty:
            return ("missing-parameter",)
        if value is kf.logger:
            # Standard SDK diagnostics do not supply layout inputs. Custom
            # logger objects/callbacks still need their own supported boundary.
            return ("sdk-logger",)
        if value is self.layout or any(value is peer for peer in self.peer_layouts):
            return ("KCLayout", value.name)
        if isinstance(value, ModuleType) and value.__name__.split(".")[0] in SDK_PACKAGES:
            return ("sdk-module", value.__name__)
        if isinstance(value, ModuleType) and self.installed_module(value):
            return ("installed-module", value.__name__)
        if isinstance(value, FunctionType):
            name = ".".join(qualified(value))
            if name in self.registered_factory_names:
                return ("registered-factory", name)
            if self.installed_module(sys.modules.get(value.__module__)):
                return ("installed-callable", qualified(value))
            if id(value) in self.active_functions:
                raise NotPersistable("recursive callable captures need an explicit boundary")
            if value.__module__.split(".")[0] not in SDK_PACKAGES:
                self.active_functions.add(id(value))
                try:
                    description, roots = recipe(value, adapt=self.adapt)
                    self.roots.update(roots)
                    return ("recipe", description)
                finally:
                    self.active_functions.remove(id(value))
            if value.__module__.split(".")[0] in SDK_PACKAGES:
                # Check the loaded body even for editable SDK installations.
                self.active_functions.add(id(value))
                try:
                    path = Path(value.__code__.co_filename).resolve()
                    self.roots.add(discover_root(path))
                    compiled = compiled_source(str(path), path.read_bytes(), sys.flags.optimize)
                    if not any(code.co_qualname == value.__code__.co_qualname and code_bytes(code) == code_bytes(value.__code__) for code in codes(compiled)):
                        raise SourceContextChanged(f"loaded SDK callable differs from source: {name}")
                    return ("sdk-callable", name, hashlib.sha256(code_bytes(value.__code__)).hexdigest(),
                            self.encode(value.__defaults__), self.encode(value.__kwdefaults__),
                            tuple(self.encode(cell.cell_contents) for cell in value.__closure__ or ()))
                finally:
                    self.active_functions.remove(id(value))
        if isinstance(value, Enum):
            return ("enum", qualified(type(value)), self.encode(value.value))
        if isinstance(value, Path):
            return ("path", str(value))
        if isinstance(value, UnionType):
            return ("union", tuple(self.encode(item) for item in get_args(value)))
        if get_origin(value) is Annotated:
            return ("annotated", tuple(self.encode(item) for item in get_args(value)))
        if isinstance(value, TypeAliasType) and value.__module__.split(".")[0] in SDK_PACKAGES:
            return ("sdk-type-alias", value.__module__, value.__name__)
        if isinstance(value, kf.decorators.SignatureParams):
            return ("signature-parameters", self.encode(value.defaults), value.names, value.units)
        if isinstance(value, type) and value.__module__.split(".")[0] in SDK_PACKAGES | {"builtins"}:
            return ("type", qualified(value))
        if isinstance(value, operator.attrgetter):
            return ("attrgetter", value.__reduce__()[1])
        if isinstance(value, partial):
            return ("partial", self.encode(value.func), self.encode(value.args), self.encode(value.keywords))
        if isinstance(value, kf.kdb.LayerInfo):
            return ("LayerInfo", value.layer, value.datatype, value.name)
        if type(value) in (kf.CrossSection, kf.DCrossSection,
                           kf.AsymmetricCrossSection, kf.DAsymmetricCrossSection):
            return ("cross-section", qualified(type(value)), self.encode(value.kcl),
                    self.encode(value.base))
        if isinstance(value, BaseModel):
            if id(value) in self.active_models:
                raise NotPersistable("cyclic configuration model")
            path = inspect.getsourcefile(type(value))
            if path:
                self.roots.add(discover_root(path))
            for cls in type(value).__mro__:
                if cls.__module__.split(".")[0] in SDK_PACKAGES | {"pydantic", "builtins"}:
                    continue
                # Python 3.14's compiler supplies the lazy annotation thunk;
                # Pydantic stores it as __annotate_func__ on field-only models.
                # Actual field values and the class source root are tracked here.
                # It is not a user-defined configuration method.
                members = ((name, member) for name, member in vars(cls).items()
                           if not (name == "__annotate_func__"
                                   and isinstance(member, FunctionType)
                                   and member.__code__.co_name == "__annotate__"
                                   and member.__qualname__ == f"{cls.__qualname__}.__annotate__"))
                if any(isinstance(member, (FunctionType, classmethod, staticmethod, property)) for _, member in members):
                    raise NotPersistable("custom configuration-model behavior needs an explicit recipe boundary")
            self.active_models.add(id(value))
            try:
                return ("model", qualified(type(value)),
                        {name: self.encode(getattr(value, name)) for name in type(value).model_fields},
                        self.encode(value.model_extra))
            finally:
                self.active_models.remove(id(value))
        return NotImplemented

    def installed_module(self, module):
        if not isinstance(module, ModuleType) or module.__name__.split(".")[0] not in self.immutable_modules:
            return False
        path = getattr(module, "__file__", None)
        return path is not None and any(Path(path).resolve().is_relative_to(root) for root in self.site_roots)

    def encode(self, value):
        return canonical(value, adapt=self.adapt)

    def describe_factory(self, factory):
        description, roots = recipe(factory._f_orig, adapt=self.adapt)
        self.roots.update(roots)
        outer = closure_values(self.originals[factory.qualified_name])
        cached = outer["wrapped_cell"]
        # cachetools preserves __wrapped__; use the original KFactory body.
        inner = closure_values(inspect.unwrap(cached))
        options = {name: value for name, value in inner.items() if name not in {"f", "kcl", "self"}}
        options["info"] = outer.get("info")
        options["name"] = factory.name
        options["tags"] = factory.tags
        options["ports_definition"] = factory.ports_definition
        options["schematic_function"] = factory._f_schematic
        options["signature"] = tuple(
            (name, parameter.kind.value, parameter.default, parameter.annotation)
            for name, parameter in factory.signature.parameters.items()
        )
        # The actual cache-key closure owns custom type/hint serializers.
        key = closure_values(cached).get("key")
        options["serializers"] = closure_values(key) if key else None
        return (description, {name: self.encode(value) for name, value in options.items()})

    def describe(self):
        if self.layout.factories.all() != self.factories:
            raise SourceContextChanged("factory registration changed; restart the context")
        if self.layout.virtual_factories.all():
            raise NotPersistable("virtual factories need their own adapter boundary")
        if self.layout._metadata_registry._records:
            raise NotPersistable("metadata providers need a recorded callable boundary")
        configuration = {
            name: self.encode(getattr(self.layout, name))
            for name in type(self.layout).model_fields if name not in LAYOUT_RUNTIME | {"layer_enclosures"}
        }
        configuration["layer_enclosures"] = self.encode({name: value for name, value in self.layout.layer_enclosures.root.items() if name not in self.output_enclosures})
        configuration["native_layers"] = {index: info for index, info in self.layer_table().items() if index not in self.output_layers}
        configuration["dbu"] = self.layout.dbu
        configuration["extra"] = self.encode(self.layout.model_extra)
        configuration["cross_sections"] = self.encode({name: value for name, value in self.layout.cross_sections.cross_sections.items() if name not in self.output_cross_sections})
        configuration["rename_function"] = self.encode(self.layout.rename_function)
        configuration["routing_strategies"] = self.encode(self.layout.routing_strategies)
        # Generic factories normalize inputs and delegate to registered cell
        # factories (the standard straight factory uses this path). They own no
        # independent result cache. Track their source/captures as configuration.
        configuration["generic_factories"] = self.encode(self.layout.generic_factories)
        configuration["global"] = {
            name: self.encode(getattr(kf.config, name)) for name in type(kf.config).model_fields
            if name not in CONFIG_RUNTIME
        }
        configuration["global_extra"] = self.encode(kf.config.model_extra)
        factories = {}
        self.unsupported = {}
        for factory in self.factories:
            try:
                if not factory.persistent:
                    raise NotPersistable("factory explicitly opts out of persistence")
                factories[factory.qualified_name] = self.describe_factory(factory)
            except NotPersistable as reason:
                self.unsupported[factory.qualified_name] = str(reason)
                factories[factory.qualified_name] = ("uncached", str(reason))
        return (configuration, factories)

    def accept_output_registrations(self):
        """Track newly generated layer/port definitions as build outputs."""
        current_layers = self.layer_table()
        if any(current_layers.get(index) != info for index, info in self.known_layers.items()):
            raise SourceContextChanged("existing layer definition changed; restart the context")
        for index, info in current_layers.items():
            if index not in self.known_layers:
                self.output_layers[index] = self.known_layers[index] = info
        for current, known, outputs in (
            (self.layout.cross_sections.cross_sections, self.known_cross_sections, self.output_cross_sections),
            (self.layout.layer_enclosures.root, self.known_enclosures, self.output_enclosures),
        ):
            for name, expected in known.items():
                if name not in current or self.encode(current[name]) != expected:
                    raise SourceContextChanged("existing port-definition registration changed")
            for name, value in current.items():
                if name not in known:
                    outputs[name] = known[name] = self.encode(value)

    def check(self):
        if self.session is not None:
            return self.session.check()
        self.check_local()

    def check_local(self):
        try:
            current_layers = self.layer_table()
            if any(current_layers.get(index) != info for index, info in self.output_layers.items()):
                raise SourceContextChanged("generated layer definition changed; restart the context")
            for current, outputs in ((self.layout.cross_sections.cross_sections, self.output_cross_sections), (self.layout.layer_enclosures.root, self.output_enclosures)):
                if any(name not in current or self.encode(current[name]) != value for name, value in outputs.items()):
                    raise SourceContextChanged("generated port-definition registration changed")
            description = self.describe()
            if description != self.description or manifest(self.roots) != self.baseline:
                raise SourceContextChanged("source, configuration or recipe changed; restart the context")
        except (OSError, SyntaxError) as error:
            raise SourceContextChanged("tracked source is no longer readable") from error

    def layer_table(self):
        return {index: self.encode(self.layout.get_info(index)) for index in self.layout.layer_indexes()}
