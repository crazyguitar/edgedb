##
# Copyright (c) 2012, 2013 Sprymix Inc.
# All rights reserved.
#
# See LICENSE for details.
##


import base64
import errno
import hashlib
import itertools
import os
import re
import shutil
import uuid

from metamagic.utils import buckets as base_buckets, config, abc

from metamagic.spin.protocols.http import types as http_types
from metamagic.spin.core import _coroutine
from metamagic.spin import abstractcoroutine
from metamagic import node

from .exceptions import FSError


class BackendError(FSError):
    pass


class Backend(base_buckets.Backend):
    pass


class BaseFSBackend(Backend):
    auto_create_path = config.cvalue(True, type=bool,
                                     doc='whether to create the "path" directory '
                                         'if it missing or not')

    umask = config.cvalue(None, type=int,
                          doc='umask with wich files will be stored. If unset then umask config '
                              'from default node or metamagic.node.Node will be used')

    def __init__(self, *, path, **kwargs):
        """
        Parameters:

        path             - fs path to where the files can be stored
        """

        super().__init__(**kwargs)

        if self.umask is None:
            node_cls = node.Node.default_cls or node.Node
            self.umask = node_cls.umask

        self.path = os.path.abspath(path)

        if not os.path.exists(self.path):
            if self.auto_create_path:
                os.mkdir(self.path, 0o777 - self.umask)

            if not os.path.exists(self.path):
                raise BackendError('unable to create directory {!r}'.format(self.path))


class FSBackend(BaseFSBackend):
    _re_escape = re.compile(r'[^\w\-\._]')

    # Don't change this constant, as FS in all existing project will need to be
    # converted
    _FN_LEN_LIMIT = 75

    def __init__(self, *args, pub_path, **kwargs):
        super().__init__(*args, **kwargs)
        self.pub_path = pub_path

    def escape_filename(self, filename):
        return self._re_escape.sub('_', filename).strip('-')

    def _get_base_name(self, bucket, id, filename):
        assert isinstance(id, uuid.UUID)

        base = str(bucket.id)

        new_id = base64.b32encode(hashlib.md5(id.bytes).digest()).decode('ascii')

        base_filename = id.hex + '_'
        filename = base_filename + filename

        if len(filename) > self._FN_LEN_LIMIT:
            if '.' in filename:
                extension = filename.rpartition('.')[2]
                limit = self._FN_LEN_LIMIT - len(extension) - 1
                if limit <= 0:
                    filename = filename[:self._FN_LEN_LIMIT]
                else:
                    filename = filename[:limit] + '.' + extension
            else:
                filename = filename[:self._FN_LEN_LIMIT]

        return os.path.join(base, new_id[:2], new_id[2:4], filename)

    def _get_path(self, bucket, id, filename, allow_rewrite):
        base = self._get_base_name(bucket, id, self.escape_filename(filename))
        path = os.path.join(self.path, base)

        if os.path.exists(path):
            if allow_rewrite:
                if os.path.islink(path):
                    os.unlink(path)
                else:
                    os.remove(path)
            else:
                raise BackendError('file names collision: {} already exists'.format(path))

        dir = os.path.dirname(path)
        os.makedirs(dir, exist_ok=True, mode=(0o777 - self.umask))
        return path

    def _after_save(self, path):
        os.chmod(path, 0o666 - self.umask)

    @_coroutine
    def store_http_file(self, bucket, id, file, *, allow_rewrite=False):
        if not isinstance(file, http_types.File):
            raise BackendError('unsupported file object: expected instance of'
                               'spin.http.types.File, got {!r}'.format(file))

        path = self._get_path(bucket, id, file.filename, allow_rewrite)
        yield file.save_to(path)
        self._after_save(path)

    @_coroutine
    def store_file(self, bucket, id, filename, *, name=None, allow_rewrite=False):
        if not os.path.isfile(filename):
            raise BackendError('unable to locate file {!r}'.format(filename))

        if name is None:
            name = os.path.basename(filename)

        path = self._get_path(bucket, id, name, allow_rewrite)

        try:
            os.link(filename, path)
        except OSError as e:
            if e.errno == errno.EXDEV:
                shutil.copy(filename, path)
            else:
                raise

        self._after_save(path)

    def get_file_path(self, bucket, id, filename):
        filename = self.escape_filename(filename)
        return os.path.join(self.path, self._get_base_name(bucket, id, filename))

    def get_file_pub_url(self, bucket, id, filename):
        filename = self.escape_filename(filename)
        return os.path.join(self.pub_path, self._get_base_name(bucket, id, filename))