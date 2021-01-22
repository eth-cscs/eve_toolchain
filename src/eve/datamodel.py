# -*- coding: utf-8 -*-
#
# Eve Toolchain - GT4Py Project - GridTools Framework
#
# Copyright (c) 2020, CSCS - Swiss National Supercomputing Center, ETH Zurich
# All rights reserved.
#
# This file is part of the GT4Py project and the GridTools framework.
# GT4Py is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the
# Free Software Foundation, either version 3 of the License, or any later
# version. See the LICENSE.txt file at the top-l directory of this
# distribution for a copy of the license or check <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""DataModel class and utils."""


from __future__ import annotations

import collections
import dataclasses
import sys
import types
import typing
from typing import Any, Callable, ClassVar, Dict, Generic, Optional, Tuple, Type

import attr


from . import utils  # isort:skip
from .concepts import NOTHING  # isort:skip


_ATTR_SETTINGS = types.MappingProxyType({"auto_attribs": True, "slots": False, "kw_only": True})
_FIELD_VALIDATOR_TAG = "__field_validator_tag"
_ROOT_VALIDATOR_TAG = "__root_validator_tag"
_ROOT_VALIDATORS_NAME = "__datamodel_validators__"


class _SENTINEL:
    ...


AUTO_CONVERTER = _SENTINEL()


# --- Validators ---
@attr.define
class _TupleValidator:
    """
    Compose many validators to a single one.
    """

    validators: Tuple[Callable]
    tuple_type: Type[Tuple]

    def __call__(self, instance, attribute, value):
        if not isinstance(value, self.tuple_type):
            raise TypeError(
                f"In '{attribute.name}' validation, got '{value}' that is a {type(value)} instead of {self.tuple_type}."
            )
        if len(value) != len(self.validators):
            raise TypeError(
                f"In '{attribute.name}' validation, got '{value}' tuple which contains {len(value)} elements instead of {len(self.validators)}."
            )

        _i = None
        item_value = ""
        try:
            for _i, (item_value, item_validator) in enumerate(zip(value, self.validators)):
                item_validator(instance, attribute, item_value)
        except Exception as e:
            raise TypeError(
                f"In '{attribute.name}' validation, tuple '{value}' contains invalid value '{item_value}' at position {_i}."
            ) from e


@attr.define
class _OrValidator:
    """
    Compose many validators to a single one.
    """

    validators: Tuple[Callable]
    error_type: Type[Exception]

    def __call__(self, instance, attribute, value):
        passed = False
        for v in self.validators:
            try:
                v(instance, attribute, value)
                passed = True
                break
            except Exception:
                pass

        if not passed:
            raise self.error_type(
                f"In '{attribute.name}' validation, provided value '{value}' fails for all the possible validators."
            )


@attr.define
class _LiteralValidator:
    """
    Literal validator.
    """

    literal: Any

    def __call__(self, instance, attribute, value):
        if isinstance(self.literal, bool):
            valid = value is self.literal
        else:
            valid = value == self.literal
        if not valid:
            raise ValueError(
                f"Provided value '{value}' field does not match {self.literal} during '{attribute.name}' validation."
            )


def empty_attrs_validator(instance, attribute, value):
    pass


def or_attrs_validator(*validators, error_type: Type[Exception]):
    vals = tuple(utils.flatten(validators))
    if len(vals) == 1:
        return vals[0]
    else:
        return _OrValidator(vals, error_type)


def literal_type_attrs_validator(type_args):
    return or_attrs_validator([_LiteralValidator(t) for t in type_args], error_type=ValueError)


def tuple_type_attrs_validator(type_args, tuple_type=tuple):
    if len(type_args) == 2 and (type_args[1] is Ellipsis):
        member_type_hint = type_args[0]
        return attr.validators.deep_iterable(
            member_validator=strict_type_attrs_validator(member_type_hint),
            iterable_validator=attr.validators.instance_of(tuple_type),
        )
    else:
        return _TupleValidator(tuple(strict_type_attrs_validator(t) for t in type_args), tuple_type)


def union_type_attrs_validator(type_args):
    if len(type_args) == 2 and (type_args[1] is type(None)):
        non_optional_validator = strict_type_attrs_validator(type_args[0])
        return attr.validators.optional(non_optional_validator)
    else:
        return or_attrs_validator(
            [strict_type_attrs_validator(t) for t in type_args], error_type=TypeError
        )


def strict_type_attrs_validator(type_hint):
    origin_type = typing.get_origin(type_hint)
    type_args = typing.get_args(type_hint)

    # print(f"strict_type_attrs_validator({type_hint}): {type_hint=}, {origin_type=}, {type_args=}")

    if isinstance(type_hint, type) and not type_args:
        return attr.validators.instance_of(type_hint)

    elif isinstance(type_hint, typing.TypeVar):
        if type_hint.__bound__:
            return attr.validators.instance_of(type_hint.__bound__)
        else:
            return empty_attrs_validator

    elif type_hint is Any:
        return empty_attrs_validator

    elif origin_type is typing.Literal:
        return literal_type_attrs_validator(type_args)

    elif origin_type is typing.Union:
        return union_type_attrs_validator(type_args)

    elif isinstance(origin_type, type):

        if issubclass(origin_type, tuple):
            return tuple_type_attrs_validator(type_args, origin_type)

        elif issubclass(origin_type, (collections.abc.Sequence, collections.abc.Set)):
            assert len(type_args) == 1
            member_type_hint = type_args[0]
            return attr.validators.deep_iterable(
                member_validator=strict_type_attrs_validator(member_type_hint),
                iterable_validator=attr.validators.instance_of(origin_type),
            )

        elif issubclass(origin_type, collections.abc.Mapping):
            assert len(type_args) == 2
            key_type_hint, value_type_hint = type_args
            return attr.validators.deep_mapping(
                key_validator=strict_type_attrs_validator(key_type_hint),
                value_validator=strict_type_attrs_validator(value_type_hint),
                mapping_validator=attr.validators.instance_of(origin_type),
            )

    else:
        raise TypeError(f"Type description '{type_hint}' is not supported.")


# --- DataModel ---
def _get_attribute_from_bases(
    name: str, mro: Tuple[Type], annotations: Optional[Dict[str, Any]] = None
) -> Optional[attr.Attribute]:
    for base in mro:
        for base_field_attrib in getattr(base, "__attrs_attrs__", []):
            if base_field_attrib.name == name:
                if annotations is not None:
                    annotations[name] = base.__annotations__[name]
                return base_field_attrib

    return None


def _make_counting_attr_from_attr(field_attr, *, include_type=False, **kwargs):
    members = [
        "default",
        "validator",
        "repr",
        "eq",
        "order",
        "hash",
        "init",
        "metadata",
        "converter",
        "kw_only",
        "on_setattr",
    ]
    if include_type:
        members.append("type")

    return attr.ib(**{key: getattr(field_attr, key) for key in members}, **kwargs)


def _make_dataclass_field_from_attr(field_attr):
    MISSING = getattr(dataclasses, "MISSING", NOTHING)
    default = MISSING
    default_factory = MISSING
    if isinstance(field_attr.default, attr.Factory):
        default_factory = field_attr.default.factory
    elif field_attr.default is not attr.NOTHING:
        default = field_attr.default

    assert field_attr.eq == field_attr.order  # dataclasses.compare == (attr.eq and attr.order)

    dataclasses_field = dataclasses.Field(
        default=default,
        default_factory=default_factory,
        init=field_attr.init,
        repr=field_attr.repr if not callable(field_attr.repr) else None,
        hash=field_attr.hash,
        compare=field_attr.eq,
        metadata=field_attr.metadata,
    )
    dataclasses_field.name = field_attr.name
    dataclasses_field.type = field_attr.type

    return dataclasses_field


def _make_non_instantiable_init():
    def __init__(self, *args, **kwargs):
        raise TypeError(f"Trying to instantiate '{type(self).__name__}' abstract class.")

    return __init__


def _make_post_init(has_post_init):
    if has_post_init:

        def __attrs_post_init__(self):
            if attr._config._run_validators is True:
                for validator in type(self).__datamodel_validators__:
                    validator.__get__(self)(self)
                self.__post_init__()

    else:

        def __attrs_post_init__(self):
            if attr._config._run_validators is True:
                for validator in type(self).__datamodel_validators__:
                    validator.__get__(self)(self)

    return __attrs_post_init__


def _make_data_model_class_getitem():
    def __class_getitem__(cls, args):
        type_args = args if isinstance(args, tuple) else (args,)
        return cls.__concretize__(*type_args)

    return classmethod(__class_getitem__)


def _make_data_model_concretize():
    @utils.optional_lru_cache(maxsize=None, typed=True)
    def __concretize__(
        cls,
        *type_args,
        class_name=None,
        module=None,
        overwrite_definition=True,
        support_pickling=True,
    ):
        # Validate generic specialization
        if not cls.__is_generic__:
            raise TypeError(f"'{cls.__name__}' is not a generic model class.")
        if not all(isinstance(t, (type, typing.TypeVar)) for t in type_args):
            raise TypeError(
                f"Only 'type' and 'typing.TypeVar' values can be passed as arguments "
                f"to instantiate a generic model class (received: {type_args})."
            )
        if len(type_args) != len(cls.__parameters__):
            raise TypeError(
                f"Instantiating '{cls.__name__}' generic model with a wrong number of parameters "
                f"({len(type_args)} used, {len(cls.__parameters__)} expected)."
            )

        # Get actual types for generic fields
        type_params_map = dict(zip(cls.__parameters__, type_args))
        print(f"CONCRETIZE: {cls.__parameters__=}, {type_args=}, {type_params_map=}")
        concrete_annotations = {}
        for f_name, f_type in typing.get_type_hints(cls).items():
            if isinstance(f_type, typing.TypeVar) and f_type in type_params_map:
                concrete_annotations[f_name] = type_params_map[f_type]
                continue

            origin = typing.get_origin(f_type)
            if origin not in (None, ClassVar) and getattr(f_type, "__parameters__", None):
                concrete_type_args = tuple([type_params_map[p] for p in f_type.__parameters__])
                concrete_annotations[f_name] = f_type[concrete_type_args]

        # Create new concrete class
        namespace = {
            "__annotations__": concrete_annotations,
            "__module__": module if module else cls.__module__,
        }

        if not class_name:
            arg_names = [
                type_params_map[tp_var].__name__ if tp_var in type_params_map else tp_var.__name__
                for tp_var in cls.__parameters__
            ]
            class_name = f"{cls.__name__}__{'_'.join(arg_names)}"

        # Fix Generic typing variables
        alias = typing._GenericAlias(cls, type_args)
        bases = (cls, Generic[alias.__parameters__]) if alias.__parameters__ else (cls,)

        # concrete_cls = type(class_name, bases, namespace)
        concrete_cls = types.new_class(class_name, bases, exec_body=lambda ns: ns.update(namespace))
        for attribute in ("__origin__", "__args__"):
            setattr(concrete_cls, attribute, getattr(alias, attribute))
        # concrete_cls.__orig_bases__ = cls
        assert concrete_cls.__module__ == module or not module

        # For pickling to work, the new class has to be added to the proper module
        if support_pickling:
            reference_module_globals = sys.modules[concrete_cls.__module__].__dict__
            if (
                overwrite_definition is False
                and reference_module_globals.get(class_name, concrete_cls) is not concrete_cls
            ):
                raise TypeError(
                    f"Existing '{class_name}' symbol in module '{module}'"
                    "contains a reference to a different object."
                )
            reference_module_globals[class_name] = concrete_cls

        return concrete_cls

    return classmethod(__concretize__)


def field(
    *,
    default=NOTHING,
    default_factory=NOTHING,
    converter=None,
    init=True,
    repr=True,
    hash=None,
    compare=True,
    metadata=None,
):
    """Return an object to define dataclass fields."""

    defaults_kwargs = {}
    if default is not NOTHING:
        defaults_kwargs["default"] = default
    if default_factory is not NOTHING:
        if "default" in defaults_kwargs:
            raise ValueError("Cannot specify both default and default_factory.")
        defaults_kwargs["factory"] = default_factory

    if isinstance(converter, bool):
        converter = AUTO_CONVERTER if converter is True else None

    return attr.ib(
        **defaults_kwargs,
        converter=converter,
        init=init,
        repr=repr,
        hash=hash,
        eq=compare,
        order=compare,
        metadata=metadata,
    )


def validator(__name: str):
    assert isinstance(__name, str)

    def _field_validator_maker(func):
        setattr(func, _FIELD_VALIDATOR_TAG, __name)
        return func

    return _field_validator_maker


def root_validator(func):
    cls_method = classmethod(func)
    setattr(cls_method, _ROOT_VALIDATOR_TAG, None)
    return cls_method


def datamodel(
    cls=None,
    /,
    *,
    init=True,
    repr=True,
    eq=True,
    order=False,
    unsafe_hash=False,
    frozen=False,
    instantiable: bool = True,
):
    """Returns the same class as was passed in, with dunder methods
    added based on the fields defined in the class.
    Examines PEP 526 __annotations__ to determine fields.
    If init is true, an __init__() method is added to the class. If
    repr is true, a __repr__() method is added. If order is true, rich
    comparison dunder methods are added. If unsafe_hash is true, a
    __hash__() method function is added. If frozen is true, fields may
    not be assigned to after instance creation.
    """

    mro_bases = cls.__mro__[1:]
    resolved_annotations = typing.get_type_hints(cls)

    # Create attr.ibs for annotated fields (excluding ClassVars)
    if "__annotations__" not in cls.__dict__:
        cls.__annotations__ = {}
    annotations = cls.__dict__["__annotations__"]

    # Create attrib definitions with automatic type validators (and converters)
    # for the annotated fields. The original annotations are used for iteration
    # since the resolved annotations may also contain superclasses' annotations
    for key in annotations:
        type_hint = resolved_annotations[key]
        if typing.get_origin(type_hint) is not ClassVar:
            type_validator = strict_type_attrs_validator(type_hint)
            if key not in cls.__dict__:
                setattr(cls, key, attr.ib(validator=type_validator))
            elif not isinstance(cls.__dict__[key], attr._make._CountingAttr):
                setattr(cls, key, attr.ib(default=cls.__dict__[key], validator=type_validator))
            else:
                # A field() function has been used to customize the definition:
                # prepend the type validator to the list of provided validators (if any)
                cls.__dict__[key]._validator = (
                    type_validator
                    if cls.__dict__[key]._validator is None
                    else attr._make.and_(type_validator, cls.__dict__[key]._validator)
                )

                # If requested, add auto converter
                if cls.__dict__[key].converter is AUTO_CONVERTER:
                    cls.__dict__[key].converter = _make_type_coercer(type_hint)

    # Verify that there are not fields without type annotation
    for key, value in cls.__dict__.items():
        if isinstance(value, attr._make._CountingAttr) and (
            key not in annotations or typing.get_origin(resolved_annotations[key]) is ClassVar
        ):
            raise TypeError(f"Missing type annotation in '{key}' field.")

    # Collect validators: root validators from bases
    root_validators = []
    for base in reversed(mro_bases):
        for validator in getattr(base, _ROOT_VALIDATORS_NAME, []):
            if validator not in root_validators:
                root_validators.append(validator)

    # Collect validators: field and root validators in current class
    field_validators = {}
    for _, member in cls.__dict__.items():
        if hasattr(member, _FIELD_VALIDATOR_TAG):
            field_name = getattr(member, _FIELD_VALIDATOR_TAG)
            delattr(member, _FIELD_VALIDATOR_TAG)
            field_validators[field_name] = member
        elif hasattr(member, _ROOT_VALIDATOR_TAG):
            delattr(member, _ROOT_VALIDATOR_TAG)
            root_validators.append(member)

    # Add collected field validators
    for field_name, field_validator in field_validators.items():
        field_attrib = cls.__dict__.get(field_name, None)
        if not field_attrib:
            # Field has not been defined in the current class namespace,
            # look for field definition in the base classes.
            base_field_attr = _get_attribute_from_bases(field_name, mro_bases, annotations)
            if base_field_attr:
                # Create a new field in the current class cloning the existing
                # definition and add the new validator (attrs recommendation)
                field_attrib = _make_counting_attr_from_attr(base_field_attr,)
                setattr(cls, field_name, field_attrib)
            else:
                raise TypeError(f"Validator assigned to non existing '{field_name}' field.")

        # Add field validator using field_attr.validator
        assert isinstance(field_attrib, attr._make._CountingAttr)
        field_attrib.validator(field_validator)

    setattr(cls, _ROOT_VALIDATORS_NAME, tuple(root_validators))

    # Create __init__
    if "__init__" in cls.__dict__:
        raise TypeError(
            "DataModels do not support custom '__init__' methods, use '__post_init__' instead."
        )

    has_post_init = "__post_init__" in cls.__dict__
    if not instantiable:
        cls.__init__ = _make_non_instantiable_init()
    else:
        # For dataclasses emulation, __attrs_post_init__ calls __post_init__ (if it exists)
        cls.__attrs_post_init__ = _make_post_init(has_post_init)

    # Add concrete instantiation methods
    cls.__class_getitem__ = _make_data_model_class_getitem()
    cls.__concretize__ = _make_data_model_concretize()

    # Add other extra members
    cls.__is_generic__ = hasattr(cls, "__parameters__") and len(cls.__parameters__) > 0
    cls.__is_instantiable__ = bool(instantiable)

    # Convert class into an attrs class
    new_cls = attr.define(**_ATTR_SETTINGS)(cls)
    assert new_cls is cls

    # Add dataclasses compatibility
    dataclass_fields = []
    for field_attr in cls.__attrs_attrs__:
        dataclass_fields.append(_make_dataclass_field_from_attr(field_attr))
    cls.__dataclass_fields__ = tuple(dataclass_fields)

    return cls


class DataModel:
    @classmethod
    def __init_subclass__(
        cls,
        /,
        *,
        init=True,
        repr=True,
        eq=True,
        order=False,
        unsafe_hash=False,
        frozen=False,
        instantiable: bool = True,
        **kwargs,
    ):
        super().__init_subclass__(**kwargs)
        datamodel(
            cls,
            init=init,
            repr=repr,
            eq=eq,
            order=order,
            unsafe_hash=unsafe_hash,
            frozen=frozen,
            instantiable=instantiable,
        )


def _make_type_coercer(type_hint):
    # TODO: implement this method
    return type_hint if isinstance(type_hint, type) else lambda x: x


def _frozen_setattr(instance, attribute, value):
    raise attr.exceptions.FrozenAttributeError(
        f"Trying to modify immutable '{attribute.name}' attribute in '{type(instance).__name__}' instance."
    )


def _valid_setattr(instance, attribute, value):
    print("SET", attribute, value)


# _ValidatorType = Callable[[Any, attr.att Attribute[_T], _T], Any]
# _ConverterType = Callable[[Any], Any]
