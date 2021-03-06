#!/usr/bin/env python

# Copyright (c) 2013 Potential Ventures Ltd
# Copyright (c) 2013 SolarFlare Communications Inc
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#     * Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#     * Neither the name of Potential Ventures Ltd,
#       SolarFlare Communications Inc nor the
#       names of its contributors may be used to endorse or promote products
#       derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL POTENTIAL VENTURES LTD BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

# -*- coding: utf-8 -*-

import ctypes
import warnings

import cocotb
from cocotb import simulator
from cocotb.binary import BinaryValue
from cocotb.log import SimLog
from cocotb.result import TestError

# Only issue a warning for each deprecated attribute access
_deprecation_warned = set()


class SimHandleBase:
    """Base class for all simulation objects.

    We maintain a handle which we can use for GPI calls.
    """

    # For backwards compatibility we support a mapping of old member names
    # which may alias with the simulator hierarchy.  In these cases the
    # simulator result takes priority, only falling back to the python member
    # if there is no colliding object in the elaborated design.
    _compat_mapping = {
        "log"               :       "_log",
        "fullname"          :       "_fullname",
        "name"              :       "_name",
    }

    def __init__(self, handle, path):
        """
        .. Constructor. This RST comment works around sphinx-doc/sphinx#6885

        Args:
            handle (int): The GPI handle to the simulator object.
            path (str): Path to this handle, ``None`` if root.
        """
        self._handle = handle
        self._len = None  # type: int
        """The "length" (the number of elements) of the underlying object. For vectors this is the number of bits."""
        self._sub_handles = {}  # type: dict
        """Dictionary of this handle's children."""
        self._invalid_sub_handles = set()  # type: set
        """Python :class:`set` of invalid queries, for caching purposes."""
        self._name = self._handle.get_name_string()  # type: str
        """The name of an object.

        :meta public:
        """
        self._type = self._handle.get_type_string()  # type: str
        """The type of an object as a string.

        :meta public:
        """
        self._fullname = self._name + "(%s)" % self._type  # type: str
        """The name of an object with its type appended in parentheses."""
        self._path = self._name if path is None else path  # type: str
        """The path to this handle, or its name if this is the root handle.

        :meta public:
        """
        self._log = SimLog("cocotb.%s" % self._name)
        """The logging object."""
        self._log.debug("Created")
        self._def_name = self._handle.get_definition_name()  # type: str
        """The name of a GPI object's definition.

        This is the value of ``vpiDefName`` for VPI, ``vhpiNameP`` for VHPI,
        and ``mti_GetPrimaryName`` for FLI.
        Support for this depends on the specific object type and simulator used.

        :meta public:
        """
        self._def_file = self._handle.get_definition_file()  # type: str
        """The name of the file that sources the object's definition.

        This is the value of ``vpiDefFile`` for VPI, ``vhpiFileNameP`` for VHPI,
        and ``mti_GetRegionSourceName`` for FLI.
        Support for this depends on the specific object type and simulator used.

        :meta public:
        """

    def get_definition_name(self):
        return self._def_name

    def get_definition_file(self):
        return self._def_file

    def __hash__(self):
        return hash(self._handle)

    def __len__(self):
        """Return the "length" (the number of elements) of the underlying object.

        For vectors this is the number of bits.
        """
        if self._len is None:
            self._len = self._handle.get_num_elems()
        return self._len

    def __eq__(self, other):
        """Equality comparator for handles

        Example usage::

            if clk == dut.clk:
                do_something()
        """
        if not isinstance(other, SimHandleBase):
            return NotImplemented
        return self._handle == other._handle

    def __ne__(self, other):
        if not isinstance(other, SimHandleBase):
            return NotImplemented
        return self._handle != other._handle

    def __repr__(self):
        desc = self._path
        defname = self._def_name
        if defname:
            desc += " with definition "+defname
            deffile = self._def_file
            if deffile:
                desc += " (at "+deffile+")"
        return type(self).__qualname__ + "(" + desc + ")"

    def __str__(self):
        return self._path

    def __setattr__(self, name, value):
        if name in self._compat_mapping:
            if name not in _deprecation_warned:
                warnings.warn("Use of attribute %r is deprecated, use %r instead" % (name, self._compat_mapping[name]))
                _deprecation_warned.add(name)
            return setattr(self, self._compat_mapping[name], value)
        else:
            return object.__setattr__(self, name, value)

    def __getattr__(self, name):
        if name in self._compat_mapping:
            if name not in _deprecation_warned:
                warnings.warn("Use of attribute %r is deprecated, use %r instead" % (name, self._compat_mapping[name]))
                _deprecation_warned.add(name)
            return getattr(self, self._compat_mapping[name])
        else:
            return object.__getattribute__(self, name)


class RegionObject(SimHandleBase):
    """A region object, such as a scope or namespace.

    Region objects don't have values, they are effectively scopes or namespaces.
    """

    def __init__(self, handle, path):
        SimHandleBase.__init__(self, handle, path)
        self._discovered = False  # True if this object has already been discovered

    def __iter__(self):
        """Iterate over all known objects in this layer of hierarchy."""
        if not self._discovered:
            self._discover_all()

        for name, handle in self._sub_handles.items():
            if isinstance(handle, list):
                self._log.debug("Found index list length %d", len(handle))
                for subindex, subhdl in enumerate(handle):
                    if subhdl is None:
                        self._log.warning("Index %d doesn't exist in %s.%s", subindex, self._name, name)
                        continue
                    self._log.debug("Yielding index %d from %s (%s)", subindex, name, type(subhdl))
                    yield subhdl
            else:
                self._log.debug("Yielding %s (%s)", name, handle)
                yield handle

    def _discover_all(self):
        """When iterating or performing IPython tab completion, we run through ahead of
        time and discover all possible children, populating the :any:`_sub_handles`
        mapping. Hierarchy can't change after elaboration so we only have to
        do this once.
        """
        if self._discovered:
            return
        self._log.debug("Discovering all on %s", self._name)
        for thing in self._handle.iterate(simulator.OBJECTS):
            name = thing.get_name_string()
            try:
                hdl = SimHandle(thing, self._child_path(name))
            except TestError as e:
                self._log.debug("%s", e)
                continue

            try:
                key = self._sub_handle_key(name)
            except ValueError:
                self._log.debug("Unable to translate handle >%s< to a valid _sub_handle key", hdl._name)
                continue

            self._sub_handles[key] = hdl

        self._discovered = True

    def _child_path(self, name) -> str:
        """Return a string of the path of the child :any:`SimHandle` for a given *name*."""
        return self._path + "." + name

    def _sub_handle_key(self, name):
        """Translate the handle name to a key to use in :any:`_sub_handles` dictionary."""
        return name.split(".")[-1]

    def __dir__(self):
        """Permits IPython tab completion to work."""
        self._discover_all()
        return super(RegionObject, self).__dir__() + [str(k) for k in self._sub_handles]


class HierarchyObject(RegionObject):
    """Hierarchy objects are namespace/scope objects."""

    def __get_sub_handle_by_name(self, name):
        try:
            return self._sub_handles[name]
        except KeyError:
            pass

        if not self._discovered:
            self._discover_all()
            return self.__get_sub_handle_by_name(name)

        # Cache to avoid a call to the simulator if we already know the name is
        # invalid. Unclear if we care, but we had this before.
        if name in self._invalid_sub_handles:
            return None

        new_handle = self._handle.get_handle_by_name(name)

        if not new_handle:
            self._invalid_sub_handles.add(name)
            return None

        sub_handle = SimHandle(new_handle, self._child_path(name))
        self._sub_handles[name] = sub_handle
        return sub_handle

    def __setattr__(self, name, value):
        """Provide transparent access to signals via the hierarchy.

        Slightly hacky version of operator overloading in Python.

        Raise an :exc:`AttributeError` if users attempt to create new members which
        don't exist in the design.
        """

        # private attributes pass through directly
        if name.startswith("_"):
            return SimHandleBase.__setattr__(self, name, value)

        # then try handles
        sub = self.__get_sub_handle_by_name(name)
        if sub is not None:
            sub.value = value
            return

        # compat behavior
        if name in self._compat_mapping:
            return SimHandleBase.__setattr__(self, name, value)

        raise AttributeError("%s contains no object named %s" % (self._name, name))

    def __getattr__(self, name):
        """Query the simulator for an object with the specified name
        and cache the result to build a tree of objects.
        """
        if name.startswith("_"):
            return SimHandleBase.__getattr__(self, name)

        handle = self.__get_sub_handle_by_name(name)
        if handle is not None:
            return handle

        if name in self._compat_mapping:
            return SimHandleBase.__getattr__(self, name)

        raise AttributeError("%s contains no object named %s" % (self._name, name))

    def _id(self, name, extended: bool = True):
        """Query the simulator for an object with the specified *name*,
        and cache the result to build a tree of objects.

        If *extended* is ``True``, run the query only for VHDL extended identifiers.
        For Verilog, only ``extended=False`` is supported.

        :meta public:
        """
        if extended:
            name = "\\"+name+"\\"

        handle = self.__get_sub_handle_by_name(name)
        if handle is not None:
            return handle

        raise AttributeError("%s contains no object named %s" % (self._name, name))


class HierarchyArrayObject(RegionObject):
    """Hierarchy Arrays are containers of Hierarchy Objects."""

    def _sub_handle_key(self, name):
        """Translate the handle name to a key to use in :any:`_sub_handles` dictionary."""
        # This is slightly hacky, but we need to extract the index from the name
        #
        # FLI and VHPI(IUS):  _name(X) where X is the index
        # VHPI(ALDEC):        _name__X where X is the index
        # VPI:                _name[X] where X is the index
        import re
        result = re.match(r"{0}__(?P<index>\d+)$".format(self._name), name)
        if not result:
            result = re.match(r"{0}\((?P<index>\d+)\)$".format(self._name), name)
        if not result:
            result = re.match(r"{0}\[(?P<index>\d+)\]$".format(self._name), name)

        if result:
            return int(result.group("index"))
        else:
            raise ValueError("Unable to match an index pattern: {}".format(name))

    def __len__(self):
        """Return the "length" of the generate block."""
        if self._len is None:
            if not self._discovered:
                self._discover_all()

            self._len = len(self._sub_handles)
        return self._len

    def __getitem__(self, index):
        if isinstance(index, slice):
            raise IndexError("Slice indexing is not supported")
        if index in self._sub_handles:
            return self._sub_handles[index]
        new_handle = self._handle.get_handle_by_index(index)
        if not new_handle:
            raise IndexError("%s contains no object at index %d" % (self._name, index))
        path = self._path + "[" + str(index) + "]"
        self._sub_handles[index] = SimHandle(new_handle, path)
        return self._sub_handles[index]

    def _child_path(self, name):
        """Return a string of the path of the child :any:`SimHandle` for a given name."""
        index = self._sub_handle_key(name)
        return self._path + "[" + str(index) + "]"

    def __setitem__(self, index, value):
        raise TypeError("Not permissible to set %s at index %d" % (self._name, index))


class _AssignmentResult:
    """
    An object that exists solely to provide an error message if the caller
    is not aware of cocotb's meaning of ``<=``.
    """

    def __init__(self, signal, value):
        self._signal = signal
        self._value = value

    def __bool__(self):
        raise TypeError(
            "Attempted to use `{0._signal!r} <= {0._value!r}` (a cocotb "
            "delayed write) as if it were a numeric comparison. To perform "
            "comparison, use `{0._signal!r}.value <= {0._value!r}` instead."
            .format(self)
        )


class NonHierarchyObject(SimHandleBase):
    """Common base class for all non-hierarchy objects."""

    def __iter__(self):
        return iter(())

    @property
    def value(self):
        """The value of this simulation object.

        .. note::
            When setting this property, the value is stored by the :class:`~cocotb.scheduler.Scheduler`
            and all stored values are written at the same time at the end of the current simulator time step.

            Use :meth:`setimmediatevalue` to set the value immediately.
        """
        raise TypeError("Not permissible to get values of object %s of type %s" % (self._name, type(self)))

    @value.setter
    def value(self, value):
        self._set_value(value, cocotb.scheduler._schedule_write)

    def setimmediatevalue(self, value):
        """ Assign a value to this simulation object immediately. """
        def _call_now(handle, f, *args):
            f(*args)
        self._set_value(value, _call_now)

    def _set_value(self, value, call_sim):
        """ This should be overriden in subclasses.

        This is used to implement both the setter for :attr:`value`, and the
        :meth:`setimmediatevalue` method.

        ``call_sim(handle, f, *args)`` should be used to schedule simulator writes,
        rather than performing them directly as ``f(*args)``.
        """
        raise TypeError("Not permissible to set values on object %s of type %s" % (self._name, type(self)))

    def __le__(self, value):
        """Overload less-than-or-equal-to operator to provide an HDL-like shortcut.

        Example:
        >>> module.signal <= 2
        """
        self.value = value
        return _AssignmentResult(self, value)

    def __eq__(self, other):
        """Equality comparator for non-hierarchy objects

        If ``other`` is not a :class:`SimHandleBase` instance the comparision
        uses the comparison method of the ``other`` object against our
        ``.value``.
        """
        if isinstance(other, SimHandleBase):
            return SimHandleBase.__eq__(self, other)
        return self.value == other

    def __ne__(self, other):
        if isinstance(other, SimHandleBase):
            return SimHandleBase.__ne__(self, other)
        return self.value != other

    # Re-define hash because we defined __eq__
    def __hash__(self):
        return SimHandleBase.__hash__(self)


class ConstantObject(NonHierarchyObject):
    """An object which has a value that can be read, but not set.

    The value is cached in the class since it is fixed at elaboration
    time and won't change within a simulation.
    """

    def __init__(self, handle, path, handle_type):
        """
        Args:
            handle (int): The GPI handle to the simulator object.
            path (str): Path to this handle, ``None`` if root.
            handle_type: The type of the handle
                (``simulator.INTEGER``, ``simulator.ENUM``,
                ``simulator.REAL``, ``simulator.STRING``).
        """
        NonHierarchyObject.__init__(self, handle, path)
        if handle_type in [simulator.INTEGER, simulator.ENUM]:
            self._value = self._handle.get_signal_val_long()
        elif handle_type == simulator.REAL:
            self._value = self._handle.get_signal_val_real()
        elif handle_type == simulator.STRING:
            self._value = self._handle.get_signal_val_str()
        else:
            val = self._handle.get_signal_val_binstr()
            self._value = BinaryValue(n_bits=len(val))
            try:
                self._value.binstr = val
            except Exception:
                self._value = val

    def __int__(self):
        return int(self.value)

    def __float__(self):
        return float(self.value)

    @NonHierarchyObject.value.getter
    def value(self):
        """The value of this simulation object."""
        return self._value

    def __str__(self):
        if isinstance(self.value, bytes):
            StringObject._emit_str_warning(self)
            return self.value.decode('ascii')
        else:
            ModifiableObject._emit_str_warning(self)
            return str(self.value)


class NonHierarchyIndexableObject(NonHierarchyObject):
    """ A non-hierarchy indexable object.

    Getting and setting the current value of an array is done
    by iterating through sub-handles in left-to-right order.

    Given an HDL array ``arr``:

    +--------------+---------------------+--------------------------------------------------------------+
    | Verilog      | VHDL                | ``arr.value`` is equivalent to                               |
    +==============+=====================+==============================================================+
    | ``arr[4:7]`` | ``arr(4 to 7)``     | ``[arr[4].value, arr[5].value, arr[6].value, arr[7].value]`` |
    +--------------+---------------------+--------------------------------------------------------------+
    | ``arr[7:4]`` | ``arr(7 downto 4)`` | ``[arr[7].value, arr[6].value, arr[5].value, arr[4].value]`` |
    +--------------+---------------------+--------------------------------------------------------------+

    When setting the signal as in ``arr.value = ...``, the same index equivalence as noted in the table holds.

    .. warning::
        Assigning a value to a sub-handle:

        - **Wrong**: ``dut.some_array.value[0] = 1`` (gets value as a list then updates index 0)
        - **Correct**: ``dut.some_array[0].value = 1``
    """

    def __init__(self, handle, path):
        NonHierarchyObject.__init__(self, handle, path)
        self._range = self._handle.get_range()

    def __setitem__(self, index, value):
        """Provide transparent assignment to indexed array handles."""
        self[index].value = value

    def __getitem__(self, index):
        if isinstance(index, slice):
            raise IndexError("Slice indexing is not supported")
        if self._range is None:
            raise IndexError("%s is not indexable.  Unable to get object at index %d" % (self._fullname, index))
        if index in self._sub_handles:
            return self._sub_handles[index]
        new_handle = self._handle.get_handle_by_index(index)
        if not new_handle:
            raise IndexError("%s contains no object at index %d" % (self._fullname, index))
        path = self._path + "[" + str(index) + "]"
        self._sub_handles[index] = SimHandle(new_handle, path)
        return self._sub_handles[index]

    def __iter__(self):
        if self._range is None:
            return

        self._log.debug("Iterating with range [%d:%d]", self._range[0], self._range[1])
        for i in self._range_iter(self._range[0], self._range[1]):
            try:
                result = self[i]
                yield result
            except IndexError:
                continue

    def _range_iter(self, left, right):
        if left > right:
            while left >= right:
                yield left
                left = left - 1
        else:
            while left <= right:
                yield left
                left = left + 1

    @NonHierarchyObject.value.getter
    def value(self) -> list:
        # Don't use self.__iter__, because it has an unwanted `except IndexError`
        return [
            self[i].value
            for i in self._range_iter(self._range[0], self._range[1])
        ]

    def _set_value(self, value, call_sim):
        """Assign value from a list of same length to an array in left-to-right order.
        Index 0 of the list maps to the left-most index in the array.

        See the docstring for this class.
        """
        if type(value) is not list:
            raise TypeError("Assigning non-list value to object %s of type %s" % (self._name, type(self)))
        if len(value) != len(self):
            raise ValueError("Assigning list of length %d to object %s of length %d" % (
                len(value), self._name, len(self)))
        for val_idx, self_idx in enumerate(self._range_iter(self._range[0], self._range[1])):
            self[self_idx]._set_value(value[val_idx], call_sim)


class NonConstantObject(NonHierarchyIndexableObject):
    """ A non-constant object"""
    # FIXME: what is the difference to ModifiableObject? Explain in docstring.

    def drivers(self):
        """An iterator for gathering all drivers for a signal."""
        return self._handle.iterate(simulator.DRIVERS)

    def loads(self):
        """An iterator for gathering all loads on a signal."""
        return self._handle.iterate(simulator.LOADS)


class _SetAction:
    """Base class representing the type of action used while write-accessing a handle."""
    pass


class _SetValueAction(_SetAction):
    __slots__ = ("value",)
    """Base class representing the type of action used while write-accessing a handle with a value."""

    def __init__(self, value):
        self.value = value


class Deposit(_SetValueAction):
    """Action used for placing a value into a given handle."""

    def _as_gpi_args_for(self, hdl):
        return self.value, 0  # GPI_DEPOSIT


class Force(_SetValueAction):
    """Action used to force a handle to a given value until a release is applied."""

    def _as_gpi_args_for(self, hdl):
        return self.value, 1  # GPI_FORCE


class Freeze(_SetAction):
    """Action used to make a handle keep its current value until a release is used."""

    def _as_gpi_args_for(self, hdl):
        return hdl.value, 1  # GPI_FORCE


class Release(_SetAction):
    """Action used to stop the effects of a previously applied force/freeze action."""

    def _as_gpi_args_for(self, hdl):
        return 0, 2  # GPI_RELEASE


class ModifiableObject(NonConstantObject):
    """Base class for simulator objects whose values can be modified."""

    def _set_value(self, value, call_sim):
        """Set the value of the underlying simulation object to *value*.

        This operation will fail unless the handle refers to a modifiable
        object, e.g. net, signal or variable.

        We determine the library call to make based on the type of the value
        because assigning integers less than 32 bits is faster.

        Args:
            value (ctypes.Structure, cocotb.binary.BinaryValue, int, double):
                The value to drive onto the simulator object.

        Raises:
            TypeError: If target is not wide enough or has an unsupported type
                 for value assignment.
        """
        value, set_action = self._check_for_set_action(value)

        if isinstance(value, int) and value < 0x7fffffff and len(self) <= 32:
            call_sim(self, self._handle.set_signal_val_long, set_action, value)
            return
        if isinstance(value, ctypes.Structure):
            value = BinaryValue(value=cocotb.utils.pack(value), n_bits=len(self))
        elif isinstance(value, int):
            value = BinaryValue(value=value, n_bits=len(self), bigEndian=False)
        elif isinstance(value, dict):
            # We're given a dictionary with a list of values and a bit size...
            num = 0
            vallist = list(value["values"])
            vallist.reverse()
            if len(vallist) * value["bits"] != len(self):
                raise TypeError("Unable to set with array length %d of %d bit entries = %d total, target is only %d bits long" %
                                (len(value["values"]), value["bits"], len(value["values"]) * value["bits"], len(self)))

            for val in vallist:
                num = (num << value["bits"]) + val
            value = BinaryValue(value=num, n_bits=len(self), bigEndian=False)

        elif not isinstance(value, BinaryValue):
            raise TypeError(
                "Unsupported type for value assignment: {} ({!r})"
                .format(type(value), value))

        call_sim(self, self._handle.set_signal_val_binstr, set_action, value.binstr)

    def _check_for_set_action(self, value):
        if not isinstance(value, _SetAction):
            return value, 0  # GPI_DEPOSIT
        return value._as_gpi_args_for(self)

    @NonConstantObject.value.getter
    def value(self) -> BinaryValue:
        binstr = self._handle.get_signal_val_binstr()
        result = BinaryValue(binstr, len(binstr))
        return result

    def __int__(self):
        return int(self.value)

    def _emit_str_warning(self):
        warnings.warn(
            "`str({t})` is deprecated, and in future will return `{t}._path`. "
            "To get a string representation of the value, use `str({t}.value)`."
            .format(t=type(self).__qualname__),
            FutureWarning, stacklevel=3)

    def __str__(self):
        self._emit_str_warning()
        return str(self.value)


class RealObject(ModifiableObject):
    """Specific object handle for Real signals and variables."""

    def _set_value(self, value, call_sim):
        """Set the value of the underlying simulation object to value.

        This operation will fail unless the handle refers to a modifiable
        object, e.g. net, signal or variable.

        Args:
            value (float): The value to drive onto the simulator object.

        Raises:
            TypeError: If target has an unsupported type for
                real value assignment.
        """
        value, set_action = self._check_for_set_action(value)

        try:
            value = float(value)
        except ValueError:
            raise TypeError(
                "Unsupported type for real value assignment: {} ({!r})"
                .format(type(value), value))

        call_sim(self, self._handle.set_signal_val_real, set_action, value)

    @ModifiableObject.value.getter
    def value(self) -> float:
        return self._handle.get_signal_val_real()

    def __float__(self):
        return float(self.value)


class EnumObject(ModifiableObject):
    """Specific object handle for enumeration signals and variables."""

    def _set_value(self, value, call_sim):
        """Set the value of the underlying simulation object to *value*.

        This operation will fail unless the handle refers to a modifiable
        object, e.g. net, signal or variable.

        Args:
            value (int): The value to drive onto the simulator object.

        Raises:
            TypeError: If target has an unsupported type for
                 integer value assignment.
        """
        value, set_action = self._check_for_set_action(value)

        if isinstance(value, BinaryValue):
            value = int(value)
        elif not isinstance(value, int):
            raise TypeError(
                "Unsupported type for enum value assignment: {} ({!r})"
                .format(type(value), value))

        call_sim(self, self._handle.set_signal_val_long, set_action, value)

    @ModifiableObject.value.getter
    def value(self) -> int:
        return self._handle.get_signal_val_long()


class IntegerObject(ModifiableObject):
    """Specific object handle for Integer and Enum signals and variables."""

    def _set_value(self, value, call_sim):
        """Set the value of the underlying simulation object to *value*.

        This operation will fail unless the handle refers to a modifiable
        object, e.g. net, signal or variable.

        Args:
            value (int): The value to drive onto the simulator object.

        Raises:
            TypeError: If target has an unsupported type for
                 integer value assignment.
        """
        value, set_action = self._check_for_set_action(value)

        if isinstance(value, BinaryValue):
            value = int(value)
        elif not isinstance(value, int):
            raise TypeError(
                "Unsupported type for integer value assignment: {} ({!r})"
                .format(type(value), value))

        call_sim(self, self._handle.set_signal_val_long, set_action, value)

    @ModifiableObject.value.getter
    def value(self) -> int:
        return self._handle.get_signal_val_long()


class StringObject(ModifiableObject):
    """Specific object handle for String variables."""

    def _set_value(self, value, call_sim):
        """Set the value of the underlying simulation object to *value*.

        This operation will fail unless the handle refers to a modifiable
        object, e.g. net, signal or variable.

        Args:
            value (bytes): The value to drive onto the simulator object.

        Raises:
            TypeError: If target has an unsupported type for
                 string value assignment.

        .. versionchanged:: 1.4
            Takes :class:`bytes` instead of :class:`str`.
            Users are now expected to choose an encoding when using these objects.
            As a convenience, when assigning :class:`str` values, ASCII encoding will be used as a safe default.

        """
        value, set_action = self._check_for_set_action(value)

        if isinstance(value, str):
            warnings.warn(
                "Handles on string objects will soon not accept `str` objects. "
                "Please use a bytes object by encoding the string as you see fit. "
                "`str.encode('ascii')` is typically sufficient.", DeprecationWarning, stacklevel=2)
            value = value.encode('ascii')  # may throw UnicodeEncodeError

        if not isinstance(value, bytes):
            raise TypeError(
                "Unsupported type for string value assignment: {} ({!r})"
                .format(type(value), value))

        call_sim(self, self._handle.set_signal_val_str, set_action, value)

    @ModifiableObject.value.getter
    def value(self) -> bytes:
        return self._handle.get_signal_val_str()

    def _emit_str_warning(self):
        warnings.warn(
            "`str({t})` is deprecated, and in future will return `{t}._path`. "
            "To access the `bytes` value of this handle, use `{t}.value`."
            .format(t=type(self).__qualname__),
            FutureWarning, stacklevel=3)

    def __str__(self):
        self._emit_str_warning()
        return self.value.decode('ascii')


_handle2obj = {}


def SimHandle(handle, path=None):
    """Factory function to create the correct type of `SimHandle` object.

    Args:
        handle (int): The GPI handle to the simulator object.
        path (str): Path to this handle, ``None`` if root.

    Returns:
        The `SimHandle` object.

    Raises:
        TestError: If no matching object for GPI type could be found.
    """
    _type2cls = {
        simulator.MODULE:      HierarchyObject,
        simulator.STRUCTURE:   HierarchyObject,
        simulator.REG:         ModifiableObject,
        simulator.NET:         ModifiableObject,
        simulator.NETARRAY:    NonHierarchyIndexableObject,
        simulator.REAL:        RealObject,
        simulator.INTEGER:     IntegerObject,
        simulator.ENUM:        EnumObject,
        simulator.STRING:      StringObject,
        simulator.GENARRAY:    HierarchyArrayObject,
    }

    # Enforce singletons since it's possible to retrieve handles avoiding
    # the hierarchy by getting driver/load information
    global _handle2obj
    try:
        return _handle2obj[handle]
    except KeyError:
        pass

    t = handle.get_type()

    # Special case for constants
    if handle.get_const() and t not in [
        simulator.MODULE,
        simulator.STRUCTURE,
        simulator.NETARRAY,
        simulator.GENARRAY,
    ]:
        obj = ConstantObject(handle, path, t)
        _handle2obj[handle] = obj
        return obj

    if t not in _type2cls:
        raise TestError("Couldn't find a matching object for GPI type %d (path=%s)" % (t, path))
    obj = _type2cls[t](handle, path)
    _handle2obj[handle] = obj
    return obj
