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
# version. See the LICENSE.txt file at the top-level directory of this
# distribution for a copy of the license or check <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Definitions of basic Eve concepts."""


import collections.abc
import copy
import dataclasses
import enum
import operator

from .concepts import Node
from .types import IntEnum, StrEnum
from .typing import (
    Any,
    Callable,
    ClassVar,
    Collection,
    Iterable,
    List,
    MutableSequence,
    MutableSet,
    Optional,
    Tuple,
    Type,
    TypeVar,
    Union,
)
from .utils import NOTHING


AnyNode = TypeVar("AnyNode", bound=Node)
AnyTreeLeaf = Union[bool, bytes, int, float, str, IntEnum, StrEnum, Node, AnyNode]
AnyTreeNode = Union[AnyTreeLeaf, Collection[AnyTreeLeaf]]


class NodeVisitor:
    """Simple node visitor class based on :class:`ast.NodeVisitor`.

    The base class walks the tree and calls a visitor function for every
    node found. This function may return a value which is forwarded by
    the `visit` method. This class is meant to be subclassed, with the
    subclass adding visitor methods.

    Per default the visitor functions for the nodes are ``'visit_'`` +
    class name of the node. So a `BinOpExpr` node visit function would
    be `visit_BinOpExpr`. If no visitor function exists for a node,
    it tries to get a visitor function for each of its parent classes
    in the order define by the class' `__mro__` attribute. Finally,
    if no visitor function exists for a node or its parents, the
    `generic_visit` visitor is used instead. This behavior can be changed
    by overriding the `visit` method.

    Don't use the `NodeVisitor` if you want to apply changes to nodes during
    traversing. For this a special visitor exists (`NodeTransformer`) that
    allows modifications.
    """

    ATOMIC_COLLECTION_TYPES: ClassVar[Tuple[Type, ...]] = (str, bytes, bytearray)

    def visit(self, node: AnyTreeNode, **kwargs: Any) -> Any:
        visitor = self.generic_visit

        method_name = "visit_" + node.__class__.__name__
        if hasattr(self, method_name):
            visitor = getattr(self, method_name)
        elif isinstance(node, Node):
            for node_class in node.__class__.__mro__[1:]:
                method_name = "visit_" + node_class.__name__
                if hasattr(self, method_name):
                    visitor = getattr(self, method_name)
                    break

                if node_class is Node:
                    break

        return visitor(node, **kwargs)

    def generic_visit(self, node: AnyTreeNode, **kwargs: Any) -> Any:
        items: Iterable[Tuple[Any, Any]] = []
        if isinstance(node, Node):
            items = list(node.iter_children())
        elif isinstance(node, (collections.abc.Sequence, collections.abc.Set)) and not isinstance(
            node, self.ATOMIC_COLLECTION_TYPES
        ):
            items = enumerate(node)
        elif isinstance(node, collections.abc.Mapping):
            items = node.items()

        # Process selected items (if any)
        for _, value in items:
            self.visit(value, **kwargs)


class NodeTranslator(NodeVisitor):
    """`NodeVisitor` subclass to modify nodes NOT in place.

    The `NodeTranslator` will walk the tree and use the return value of
    the visitor methods to replace or remove the old node in a new copy
    of the tree. If the return value of the visitor method is
    `eve.NOTHING`, the node will be removed from its location in the
    result tree, otherwise it is replaced with the return value. In the
    default case, a `deepcopy` of the original node is returned.

    Keep in mind that if the node you're operating on has child nodes
    you must either transform the child nodes yourself or call the
    :meth:`generic_visit` method for the node first.

    Usually you use the transformer like this::

       node = YourTranslator().visit(node)
    """

    def __init__(self, *, memo: dict = None, **kwargs: Any) -> None:
        assert memo is None or isinstance(memo, dict)
        self.memo = memo or {}

    def generic_visit(self, node: AnyTreeNode, **kwargs: Any) -> Any:
        result: Any
        if isinstance(node, (Node, collections.abc.Collection)) and not isinstance(
            node, self.ATOMIC_COLLECTION_TYPES
        ):
            tmp_items: Collection[AnyTreeNode] = []
            if isinstance(node, Node):
                tmp_items = {
                    key: self.visit(value, **kwargs) for key, value in node.iter_children()
                }
                result = node.__class__(  # type: ignore
                    **{key: value for key, value in node.iter_attributes()},
                    **{key: value for key, value in tmp_items.items() if value is not NOTHING},
                )

            elif isinstance(node, (collections.abc.Sequence, collections.abc.Set)):
                # Sequence or set: create a new container instance with the new values
                tmp_items = [self.visit(value, **kwargs) for value in node]
                result = node.__class__(  # type: ignore
                    value for value in tmp_items if value is not NOTHING  # type: ignore
                )

            elif isinstance(node, collections.abc.Mapping):
                # Mapping: create a new mapping instance with the new values
                tmp_items = {key: self.visit(value, **kwargs) for key, value in node.items()}
                result = node.__class__(  # type: ignore
                    {key: value for key, value in tmp_items.items() if value is not NOTHING}
                )

        else:
            result = copy.deepcopy(node, memo=self.memo)

        return result


class NodeModifier(NodeVisitor):
    """Simple :class:`NodeVisitor` subclass based on :class:`ast.NodeTransformer` to modify nodes in place.

    The `NodeTransformer` will walk the tree and use the return value of
    the visitor methods to replace or remove the old node. If the
    return value of the visitor method is :obj:`eve.NOTHING`,
    the node will be removed from its location, otherwise it is replaced
    with the return value. The return value may also be the original
    node, in which case no replacement takes place.

    Keep in mind that if the node you're operating on has child nodes
    you must either transform the child nodes yourself or call the
    :meth:`generic_visit` method for the node first.

    Usually you use the transformer like this::

       node = YourTransformer().visit(node)
    """

    def generic_visit(self, node: AnyTreeNode, **kwargs: Any) -> Any:
        result: Any = node
        if isinstance(node, (Node, collections.abc.Collection)) and not isinstance(
            node, self.ATOMIC_COLLECTION_TYPES
        ):
            items: Iterable[Tuple[Any, Any]] = []
            tmp_items: Collection[AnyTreeNode] = []
            set_op: Union[Callable[[Any, str, Any], None], Callable[[Any, int, Any], None]]
            del_op: Union[Callable[[Any, str], None], Callable[[Any, int], None]]

            if isinstance(node, Node):
                items = list(node.iter_children())
                set_op = setattr
                del_op = delattr
            elif isinstance(node, collections.abc.MutableSequence):
                items = enumerate(node)
                index_shift = 0

                def set_op(container: MutableSequence, idx: int, value: AnyTreeNode) -> None:
                    container[idx - index_shift] = value

                def del_op(container: MutableSequence, idx: int) -> None:
                    nonlocal index_shift
                    del container[idx - index_shift]
                    index_shift += 1

            elif isinstance(node, collections.abc.MutableSet):
                items = list(enumerate(node))

                def set_op(container: MutableSet, idx: Any, value: AnyTreeNode) -> None:
                    container.add(value)

                def del_op(container: MutableSet, idx: int) -> None:
                    container.remove(items[idx])  # type: ignore

            elif isinstance(node, collections.abc.MutableMapping):
                items = node.items()
                set_op = operator.setitem
                del_op = operator.delitem

            elif isinstance(node, (collections.abc.Sequence, collections.abc.Set)):
                # Inmutable sequence or set: create a new container instance with the new values
                tmp_items = [self.visit(value, **kwargs) for value in node]
                result = node.__class__(  # type: ignore
                    [value for value in tmp_items if value is not NOTHING]  # type: ignore
                )

            elif isinstance(node, collections.abc.Mapping):
                # Inmutable mapping: create a new mapping instance with the new values
                tmp_items = {key: self.visit(value, **kwargs) for key, value in node.items()}
                result = node.__class__(  # type: ignore
                    {key: value for key, value in tmp_items.items() if value is not NOTHING}
                )

            # Finally, in case current node object is mutable, process selected items (if any)
            for key, value in items:
                new_value = self.visit(value, **kwargs)
                if new_value is NOTHING:
                    del_op(result, key)
                elif new_value != value:
                    set_op(result, key, new_value)

        return result


@enum.unique
class PathItemKind(enum.Enum):
    CLASS = 1
    COLLECTION = 2


@dataclasses.dataclass(frozen=True)
class PathItem:
    node: AnyTreeNode
    kind: PathItemKind
    member: Union[str, int]


@dataclasses.dataclass(frozen=True)
class PathInfo:
    items: List[PathItem] = dataclasses.field(default_factory=list)

    def append(self, item: PathItem) -> "PathInfo":
        return PathInfo([*self.items, item])

    @property
    def root(self) -> Optional[PathItem]:
        return self.items[-1] if self.items else None

    @property
    def is_root(self) -> bool:
        return len(self.items) == 0

    def __iter__(self) -> Iterable[PathItem]:
        return iter(self.items)


class PathNodeVisitor:
    """Simple node visitor with path information based on :class:`ast.NodeVisitor`.
    """

    # @classmethod
    # def __init_subclass__(cls, *, path_info=False, **kwargs: Any):
    #     if path_info:

    ATOMIC_COLLECTION_TYPES = (str, bytes, bytearray)

    def visit(
        self, node: AnyTreeNode, *, path_info: Optional[PathInfo] = None, **kwargs: Any
    ) -> Any:
        if path_info is None:
            path_info = PathInfo()

        visitor = self.generic_visit
        if isinstance(node, Node):
            for node_class in node.__class__.__mro__:
                method_name = "visit_" + node_class.__name__
                if hasattr(self, method_name):
                    visitor = getattr(self, method_name)
                    break

                if node_class is Node:
                    break

        return visitor(node, path_info=path_info, **kwargs)

    def generic_visit(self, node: AnyTreeNode, *, path_info: PathInfo, **kwargs: Any) -> Any:
        items: Iterable[Tuple[Any, Any]] = []

        if isinstance(node, Node):
            items = list(node.iter_children())
            item_kind: PathItemKind = PathItemKind.CLASS
        elif isinstance(node, (collections.abc.Sequence, collections.abc.Set)) and not isinstance(
            node, self.ATOMIC_COLLECTION_TYPES
        ):
            items = enumerate(node)
            item_kind = PathItemKind.COLLECTION
        elif isinstance(node, collections.abc.Mapping):
            items = node.items()
            item_kind = PathItemKind.COLLECTION

        # Process selected items (if any)
        for name, value in items:
            path_item = PathItem(node, item_kind, name)
            self.visit(value, path_info=path_info.append(path_item), **kwargs)