##
# Copyright (c) 2011 Sprymix Inc.
# All rights reserved.
#
# See LICENSE for details.
##


import types
import decimal
import collections
import weakref
import linecache

from semantix.utils import helper
from semantix.utils.functional.dispatch import TypeDispatcher
from .. import elements

from semantix import exceptions
from semantix.utils.helper import xrepr


__all__ = 'serialize',


def no_ref_detect(func):
    """Serializer decorated with ``no_ref_detect`` will be executed wihout
    prior checking the memo if object was already serialized"""

    func.no_ref_detect = True
    return func


class serializer(TypeDispatcher):
    """Markup serializers dispatcher"""


class Context:
    """Markup serialization context.  Holds the ``memo`` set, which
    is used to avoid serializing objects that already have been serialized,
    and ``depth`` - recursion depth"""

    __slots__ = 'memo', 'depth'

    def __init__(self):
        self.memo = set()
        self.depth = 0


def serialize(obj, *, ctx=None):
    """Serialize arbitrary python object to Markup elements"""

    try:
        # Find serialization function
        #
        sr = serializer.get_handler(type=type(obj))
    except LookupError:
        raise LookupError('unable to find serializer for object {!r}'.format(obj))

    if ctx is None:
        # No context?  Perhaps, this is a top-level call to ``serialize``.
        # Initialize empty context.
        #
        ctx = Context()

    ref_detect = True
    try:
        # Was the serializer decorated with ``@no_ref_detect``?
        #
        ref_detect = not sr.no_ref_detect
    except AttributeError:
        pass

    if ref_detect:
        # OK, so if we've already serialized obj, don't do that again, just
        # return ``markup.Ref`` element.
        #
        obj_id = id(obj)
        if obj_id in ctx.memo:
            refname = '{}.{}'.format(type(obj).__module__, type(obj).__name__)
            return elements.lang.Ref(ref=obj_id, refname=refname)
        else:
            ctx.memo.add(obj_id)

    return sr(obj, ctx=ctx)


@no_ref_detect
def serialize_traceback_point(obj, *, ctx, include_source=True, source_window_size=2,
                              include_locals=False, point_cls=elements.lang.TracebackPoint):

    assert type(obj) is types.TracebackType
    assert source_window_size >= 0

    lineno = obj.tb_lineno
    name = obj.tb_frame.f_code.co_name
    filename = obj.tb_frame.f_code.co_filename

    if include_source:
        lines = []
        line_numbers = []

        sourcelines = linecache.getlines(filename, globals())
        start = max(1, lineno - source_window_size)
        end = min(len(sourcelines), lineno + source_window_size + 1)
        for i in range(start, end):
            lines.append(sourcelines[i - 1].rstrip())
            line_numbers.append(i)
    else:
        lines = line_numbers = None

    locals = None
    if include_locals:
        locals = serialize(dict(obj.tb_frame.f_locals), ctx=ctx)

    return point_cls(name=name, lineno=lineno, filename=filename,
                     lines=lines, line_numbers=line_numbers,
                     locals=locals, id=id(obj))


@serializer(handles=types.TracebackType)
def serialize_traceback(obj, *, ctx):
    result = []

    current = obj
    while current is not None:
        result.append(serialize_traceback_point(current, ctx=ctx))
        current = current.tb_next

    result.reverse()

    return elements.lang.Traceback(items=result, id=id(obj))


@serializer(handles=BaseException)
def serialize_exception(obj, *, ctx):
    try:
        # Serializing an exception to markup maybe a very time-wise
        # expensive operation.
        #
        # However, it should be safe to cache results, as we serialize
        # exceptions just before they get printed or logged, after the
        # point of adding new contexts.
        #
        return obj.__sx_markup_cached__
    except AttributeError:
        pass

    cause = obj.__cause__ or obj.__context__
    if cause is not None:
        if cause is not obj:
            # Strangely, but this really happens sometimes.
            #
            cause = serialize(cause, ctx=ctx)
        else:
            cause = None

    contexts = []
    for context in exceptions._iter_contexts(obj):
        contexts.append(serialize(context, ctx=ctx))

    if obj.__traceback__:
        traceback = elements.lang.ExceptionContext(title='Traceback',
                                                   body=[serialize(obj.__traceback__, ctx=ctx)])
        contexts.append(traceback)

    markup = elements.lang.Exception(class_module=obj.__class__.__module__,
                                     class_name=obj.__class__.__name__,
                                     msg=str(obj),
                                     contexts=contexts,
                                     cause=cause,
                                     id=id(obj))

    obj.__sx_markup_cached__ = markup
    return markup


@serializer(handles=exceptions.ExceptionContext)
def serialize_generic_exception_context(obj, *, ctx):
    msg = 'No markup serializer for {!r} context'.format(obj)
    return elements.lang.ExceptionContext(title=obj.title,
                                          body=[elements.doc.Text(text=msg)])


@serializer(handles=type(None))
@no_ref_detect
def serialize_none(obj, *, ctx):
    return elements.lang.Constants.none


@serializer(handles=bool)
@no_ref_detect
def serialize_bool(obj, *, ctx):
    if obj:
        return elements.lang.Constants.true
    else:
        return elements.lang.Constants.false


@serializer(handles=(int, float, decimal.Decimal))
@no_ref_detect
def serialize_number(obj, *, ctx):
    return elements.lang.Number(num=obj)


@serializer(handles=str)
@no_ref_detect
def serialize_str(obj, *, ctx):
    return elements.lang.String(str=obj)


@serializer(handles=(collections.UserList, list, tuple, collections.Set,
                     weakref.WeakSet, set, frozenset))
def serialize_sequence(obj, *, ctx):
    els = []
    for item in obj:
        els.append(serialize(item, ctx=ctx))
    return elements.lang.List(items=els, id=id(obj))


@serializer(handles=(dict, collections.Mapping))
def serialize_mapping(obj, *, ctx):
    map = collections.OrderedDict()
    for key, value in obj.items():
        if not isinstance(key, str):
            key = repr(key)
        map[key] = serialize(value, ctx=ctx)
    return elements.lang.Dict(items=map, id=id(obj))


@serializer(handles=object)
def serialize_uknown_object(obj, *, ctx):
    return elements.lang.Object(id=id(obj),
                                class_module=type(obj).__module__,
                                class_name=type(obj).__name__)