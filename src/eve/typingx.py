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

"""Python version independent typings."""


# flake8: noqa

from __future__ import annotations

from typing import *
from typing import IO, BinaryIO, TextIO

from typing_extensions import *  # type: ignore


AnyCallable = Callable[..., Any]
AnyNoneCallable = Callable[..., None]
AnyNoArgCallable = Callable[[], Any]


T = TypeVar("T")
V = TypeVar("V")


class NonDataDescriptorProto(Protocol[T, V]):  # type: ignore
    @overload
    def __get__(
        self, _instance: None, _owner_type: Optional[Type[T]] = None
    ) -> NonDataDescriptorProto:
        ...

    @overload
    def __get__(self, _instance: T, _owner_type: Optional[Type[T]] = None) -> V:
        ...


class DataDescriptorProto(NonDataDescriptorProto[T, V], Protocol):  # type: ignore
    def __set__(self, _instance: T, _value: V) -> None:
        ...
