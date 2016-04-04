"""Write Value Change Dump files.

This module provides :class:`VCDWriter` for writing VCD files.

"""
from __future__ import print_function
from collections import OrderedDict, Sequence
from numbers import Number
import datetime

import six
from six.moves import zip_longest


class VCDPhaseError(Exception):
    """Indicating a :class:`VCDWriter` method was called in the wrong phase.

    For example, calling :meth:`register_var()` after :meth:`close()` will
    raise this exception.

    """


class VCDWriter(object):
    """Value Change Dump writer.

    A VCD file captures time-ordered changes to the value of variables.

    :param file file: A file-like object to write the VCD data.
    :param str timescale: scale of the VCD timestamps.
    :param str date: Optional `$date` string used in the VCD header.
    :param str comment: Optional `$comment` string used in the VCD header.
    :param str version: Optional `$version` string used in the VCD header.
    :param str default_scope_type: Scope type for scopes where
            :meth:`set_scope_type()` is not called explicitly.
    :param str scope_sep: Separator for scopes specified as strings.
    :raises ValueError: for invalid timescale values

    """

    #: Valid VCD scope types.
    SCOPE_TYPES = ['begin', 'fork', 'function', 'module', 'task']

    #: Valid VCD variable types.
    VAR_TYPES = ['event', 'integer', 'parameter', 'real', 'realtime', 'reg',
                 'supply0', 'supply1', 'time', 'tri', 'triand', 'trior',
                 'trireg', 'tri0', 'tri1', 'wand', 'wire', 'wor']

    #: Valid timescale numbers.
    TIMESCALE_NUMS = [1, 10, 100]

    #: Valid timescale units.
    TIMESCALE_UNITS = ['s', 'ms', 'us', 'ns', 'ps', 'fs']

    def __init__(self, file, timescale='1 us', date=None, comment='',
                 version='', default_scope_type='module', scope_sep='.'):
        self._ofile = file
        self._check_timescale(timescale)
        self._header_keywords = {
            '$timescale': timescale,
            '$date': str(datetime.datetime.now()) if date is None else date,
            '$comment': comment,
            '$version': version,
        }
        if default_scope_type not in self.SCOPE_TYPES:
            raise ValueError('Invalid default scope type ({})'.format(
                default_scope_type))
        self._default_scope_type = default_scope_type
        self._scope_sep = scope_sep
        self._registering = True
        self._closed = False
        self._dumping = True
        self._next_var_id = 0
        self._scope_var_strs = {}
        self._scope_var_names = {}
        self._scope_types = {}
        self._ident_values = OrderedDict()
        self._prev_timestamp = 0

    def set_scope_type(self, scope, scope_type):
        """Set the scope_type for a given scope.

        The scope's type may be set to one of the valid :const:`SCOPE_TYPES`.
        VCD viewer applications may display different scope types differently.

        :param scope: The scope to set the type of.
        :type scope: str or sequence of str
        :param str scope_type: A valid scope type string.
        :raises ValueError: for invalid `scope_type`

        """
        if scope_type is not None and scope_type not in self.SCOPE_TYPES:
            raise ValueError('Invalid scope_type "{}"'.format(scope_type))
        scope_tuple = self._get_scope_tuple(scope)
        self._scope_types[scope_tuple] = scope_type

    def register_var(self, scope, name, var_type, size, init=None, ident=None):
        """Register a VCD variable and return function to change value.

        All VCD variables must be registered prior to any value changes.

        .. Note::

            The variable `name` differs from the variable's `ident`
            (identifier). The `name` (also known as `ref`) is meant to refer to
            the variable name in the code being traced and is visible in VCD
            viewer applications.  The `ident`, however, is only used within the
            VCD file and can be auto-generated (by specifying ``ident=None``)
            for most applications.

        :param scope: The hierarchical scope that the variable belongs within.
        :type scope: str or sequence of str
        :param str name: Name of the variable.
        :param str var_type: One of :const:`VAR_TYPES`.
        :param int size: Size, in bits, of the variable.
        :param init: Optional initial value; defaults to 'x'.
        :param str ident: Optional identifier for use in the VCD stream.
        :raises VCDPhaseError: if any values have been changed
        :raises ValueError: for invalid var_type value
        :raises TypeError: for invalid parameter types
        :raises KeyError: for duplicate var name
        :returns: :class:`Variable` instance appropriate for use with
                  :meth:`change()`.

        """
        if self._closed:
            raise VCDPhaseError('Cannot register after close().')
        elif not self._registering:
            raise VCDPhaseError('Cannot register after time 0.')
        elif var_type not in self.VAR_TYPES:
            raise ValueError('Invalid var_type "{}"'.format(var_type))

        scope_tuple = self._get_scope_tuple(scope)

        scope_names = self._scope_var_names.setdefault(scope_tuple, [])
        if name in scope_names:
            raise KeyError('Duplicate var {} in scope {}'.format(name, scope))

        if ident is None:
            ident = 'v' + str(self._next_var_id)

        var_str = '$var {var_type} {size} {ident} {name} $end'.format(
            var_type=var_type, size=size, ident=ident, name=name)

        if init is None:
            if var_type == 'real':
                init = 0.0
            else:
                init = 'x'

        if size == 1:
            var = ScalarVariable(ident, var_type, size)
        elif var_type == 'real':
            var = RealVariable(ident, var_type, size)
        else:
            var = VectorVariable(ident, var_type, size)

        self.change(var, 0, init)

        # Only alter state after change_func() succeeds
        self._next_var_id += 1
        self._scope_var_strs.setdefault(scope_tuple, []).append(var_str)
        scope_names.append(name)

        return var

    def register_int(self, scope, name, size=64, init='x', ident=None):
        """Register an *integer* variable.

        This is a convenience wrapper for :meth:`register_var()`.

        :rtype: :class:`VectorVariable`

        """
        return self.register_var(scope, name, 'integer', size, init, ident)

    def register_real(self, scope, name, size=64, init=0.0, ident=None):
        """Register real variable.

        This is a convenience wrapper for :meth:`register_var()`.

        :rtype: :class:`RealVariable`

        """
        return self.register_var(scope, name, 'real', size, init, ident=None)

    def register_event(self, scope, name, size=1, init='z', ident=None):
        """Register event variable.

        This is a convenience wrapper for :meth:`register_var()`.

        :rtype: :class:`ScalarVariable`

        """
        return self.register_var(scope, name, 'event', size, init, ident)

    def dump_off(self, timestamp):
        """Suspend dumping to VCD file."""
        if self._dumping and not self._registering and self._ident_values:
            self._dump_off(timestamp)
        self._dumping = False

    def _dump_off(self, timestamp):
        print('#' + str(int(timestamp)), file=self._ofile)
        print('$dumpoff', file=self._ofile)
        for ident, val_str in six.iteritems(self._ident_values):
            if val_str[0] == 'b':
                print('bx', ident, file=self._ofile)
            elif val_str[0] == 'r':
                pass  # real variables cannot have 'z' or 'x' state
            else:
                print('x', ident, sep='', file=self._ofile)
        print('$end', file=self._ofile)

    def dump_on(self, timestamp):
        """Resume dumping to VCD file."""
        if not self._dumping and not self._registering and self._ident_values:
            print('#' + str(int(timestamp)), file=self._ofile)
            self._dump_values('$dumpon')
        self._dumping = True

    def _dump_values(self, keyword):
        print(keyword, file=self._ofile)
        print(*six.itervalues(self._ident_values),
              sep='\n', file=self._ofile)
        print('$end', file=self._ofile)

    def change(self, var, timestamp, value):
        """Change variable's value in VCD stream.

        This is the fundamental behavior of a :class:`VCDWriter` instance. Each
        time a variable's value changes, this method should be called.

        The *timestamp* must be in-order relative to timestamps from previous
        calls to :meth:`change()`. It is okay to call :meth:`change()` multiple
        times with the same *timestamp*, but never with a past *timestamp*.

        .. Note::

            :meth:`change()` may be called multiple times before the timestamp
            progresses past 0. The last value change for each variable will go
            into the $dumpvars section.

        :param Variable var: :class:`Variable` instance (i.e. from
                             :meth:`register_var()`).
        :param int timestamp: Current simulation time.
        :param value: New value for *var*.
        :raises ValueError: if the value is not valid for *var*.
        :raises VCDPhaseError: if the timestamp is out of order or the
                               :class:`VCDWriter` instance is closed.

        """
        if timestamp < self._prev_timestamp:
            raise VCDPhaseError('Out of order value change ({})'.format(var))
        elif self._closed:
            raise VCDPhaseError('Cannot change value after close()')

        val_str = var.format_value(value)

        if timestamp and self._registering:
            self._finalize_registration()

        if timestamp and self._dumping:
            ts_int = int(timestamp)
            if ts_int > self._prev_timestamp:
                print('#', ts_int, sep='', file=self._ofile)
                self._prev_timestamp = ts_int
            print(val_str, file=self._ofile)
        else:
            self._ident_values[var.ident] = val_str

    def _get_scope_tuple(self, scope):
        if isinstance(scope, six.string_types):
            return tuple(scope.split(self._scope_sep))
        if isinstance(scope, Sequence):
            return tuple(scope)
        else:
            raise TypeError('Invalid scope {}'.format(scope))

    @classmethod
    def _check_timescale(cls, timescale):
        if not isinstance(timescale, six.string_types):
            raise TypeError('Invalid timescale type {}'.format(timescale))
        for num in sorted(cls.TIMESCALE_NUMS, reverse=True):
            num_str = str(num)
            if timescale.startswith(num_str):
                unit = timescale[len(num_str):].lstrip(' ')
                break
        else:
            raise ValueError('Invalid timescale num {}'.format(timescale))
        if unit not in cls.TIMESCALE_UNITS:
            raise ValueError('Invalid timescale unit "{}"'.format(unit))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self, timestamp=None):
        """Close VCD writer.

        Any buffered VCD data is flushed to the output file. After
        :meth:`close()`, no variable registration or value changes will be
        accepted.

        :param int timestamp: optional final timestamp to insert into VCD
                              stream.

        .. Note::

            The output file is not automatically closed. It is up to the user
            to ensure the output file is closed after the :class:`VCDWriter`
            instance is closed.

        """
        if not self._closed:
            self.flush(timestamp)
            self._closed = True

    def flush(self, timestamp=None):
        """Flush any buffered VCD data to output file.

        If the VCD header has not already been written, calling `flush()` will
        force the header to be written thus disallowing any further variable
        registration.

        :param int timestamp: optional timestamp to insert into VCD stream.

        """
        if self._closed:
            raise VCDPhaseError('Cannot flush() after close()')
        if self._registering:
            self._finalize_registration()
        if timestamp is not None and timestamp > self._prev_timestamp:
            print("#", int(timestamp), sep='', file=self._ofile)
        self._ofile.flush()

    def _gen_header(self):
        for kwname, kwvalue in sorted(six.iteritems(self._header_keywords)):
            if not kwvalue:
                continue
            lines = kwvalue.split('\n')
            if len(lines) == 1:
                yield '{} {} $end'.format(kwname, lines[0])
            else:
                yield kwname
                for line in lines:
                    yield '\t' + line
                yield '$end'

        prev_scope = []
        for scope in sorted(self._scope_var_strs):
            var_strs = self._scope_var_strs.pop(scope)

            for i, (prev, this) in enumerate(zip_longest(prev_scope, scope)):
                if prev != this:
                    for _ in prev_scope[i:]:
                        yield '$upscope $end'

                    for j, name in enumerate(scope[i:]):
                        scope_type = self._scope_types.get(
                            scope[:i+j+1], self._default_scope_type)
                        yield '$scope {} {} $end'.format(scope_type, name)
                    break
            else:
                assert scope != prev_scope  # pragma no cover

            for var_str in var_strs:
                yield var_str

            prev_scope = scope

        for _ in prev_scope:
            yield '$upscope $end'

        yield '$enddefinitions $end'

    def _finalize_registration(self):
        assert self._registering
        print(*self._gen_header(), sep='\n', file=self._ofile)
        if self._ident_values:
            self._dump_values('$dumpvars')
            if not self._dumping:
                self._dump_off(0)
        self._registering = False

        # This state is not needed after registration phase.
        self._header_keywords.clear()
        self._scope_types.clear()
        self._scope_var_names.clear()


class Variable(object):
    """VCD variable details needed to call :meth:`VCDWriter.change()`."""

    __slots__ = ('ident', 'type', 'size')

    def __init__(self, ident, type, size):
        #: Identifier used in VCD output stream.
        self.ident = ident
        #: VCD variable type; one of :const:`VCDWriter.VAR_TYPES`.
        self.type = type
        #: Size, in bits, of variable.
        self.size = size

    def format_value(self, value):
        """Format value change for use in VCD stream."""
        raise NotImplementedError


class ScalarVariable(Variable):
    """One-bit VCD scalar.

    This is a 4-state variable and thus may have values of 0, 1, 'z', or 'x'.

    """

    __slots__ = ()

    def format_value(self, value):
        """Format scalar value change for VCD stream.

        :param value: 1-bit (4-state) scalar value.
        :type value: str, bool, int, or None
        :raises ValueError: for invalid *value*.
        :returns: string representing value change for use in a VCD stream.

        """
        if isinstance(value, six.string_types):
            if len(value) != 1 or value not in '01xzXZ':
                raise ValueError('Invalid scalar value ({})'.format(value))
            return value + self.ident
        elif value is None:
            return 'z' + self.ident
        elif value:
            return '1' + self.ident
        else:
            return '0' + self.ident


class RealVariable(Variable):
    """Real (IEEE-754 double-precision floating point) variable.

    Values must be numeric and cannot be 'x' or 'z' states.

    """

    __slots__ = ()

    def format_value(self, value):
        """Format real value change for VCD stream.

        :param value: Numeric changed value.
        :param type: float or int
        :raises ValueError: for invalid real *value*.
        :returns: string representing value change for use in a VCD stream.

        """
        if isinstance(value, Number):
            return 'r{:.16g} {}'.format(value, self.ident)
        else:
            raise ValueError('Invalid real value ({})'.format(value))


class VectorVariable(Variable):
    """Bit vector variable type.

    This is for the various non-scalar and non-real variable types including
    integer, register, wire, etc.

    """

    __slots__ = ()

    def format_value(self, value):
        """Format value change for VCD stream.

        :param value: New value for the variable.
        :types value: int, str, or None
        :raises ValueError: for *some* invalid values.

        A *value* of `None` is the same as `'z'`.

        .. Warning::

            If *value* is of type :py:class:`str`, all characters must be one
            of `'01xzXZ'`. For the sake of performance, checking **is not**
            done to ensure value strings only contain conforming characters.
            Thus it is possible to produce invalid VCD streams with invalid
            string values.

        """
        if isinstance(value, six.integer_types):
            max_val = 1 << self.size
            if -value > (max_val >> 1) or value >= max_val:
                raise ValueError('Value ({}) not representable in {} bits'
                                 .format(value, self.size))
            if value < 0:
                value += max_val
            return 'b{:b} {}'.format(value, self.ident)
        elif value is None:
            return 'bz ' + self.ident
        elif isinstance(value, six.string_types) and len(value) <= self.size:
            # It is up to the user to ensure valid characters '01xz'...
            return 'b{} {}'.format(value, self.ident)
        else:
            raise ValueError('Invalid vector value ({})'.format(value))