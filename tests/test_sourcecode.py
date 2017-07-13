##
# Copyright (c) 2017-present MagicStack Inc.
# All rights reserved.
#
# See LICENSE for details.
##


import collections
import edgedb
import flake8  # NoQA
import os
import subprocess
import sys
import textwrap
import unittest

import docutils.nodes
import docutils.parsers
import docutils.utils
import docutils.frontend

from edgedb.lang.edgeql import parser as edgeql_parser
from edgedb.lang.graphql import parser as graphql_parser
from edgedb.lang.schema import parser as schema_parser


class TestFlake8(unittest.TestCase):

    def test_flake8(self):
        edgepath = list(edgedb.__path__)[0]
        edgepath = os.path.dirname(edgepath)

        for subdir in ['edgedb', 'tests']:  # ignore any top-level test files
            try:
                subprocess.run(
                    [sys.executable, '-m', 'flake8'],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=os.path.join(edgepath, subdir))
            except subprocess.CalledProcessError as ex:
                output = ex.output.decode()
                raise AssertionError(
                    f'flake8 validation failed:\n{output}') from None


class TestDocSnippets(unittest.TestCase):
    """Lint and validate EdgeDB documentation files.

    Checks:

    * all source code in "code-block" directives is parsed to
      check that the syntax is valid;

    * lines must be shorter than 79 characters;

    * any ReST warnings (like improper headers or broken indentation)
      are reported as errors.
    """

    MAX_LINE_LEN = 79

    CodeSnippet = collections.namedtuple(
        'CodeSnippet',
        ['filename', 'lineno', 'lang', 'code'])

    class RestructuredTextStyleError(Exception):
        pass

    class CustomDocutilsReporter(docutils.utils.Reporter):

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.lint_errors = set()

        def system_message(self, level, message, *children, **kwargs):
            skip = (
                message.startswith('Unknown interpreted text role') or
                message.startswith('No role entry for') or
                message.startswith('Unknown directive type') or
                message.startswith('No directive entry for') or
                level < 2  # Ignore DEBUG and INFO messages.
            )

            msg = super().system_message(level, message, *children, **kwargs)

            if not skip:
                self.lint_errors.add(
                    f"{message} at {msg['source']} on line "
                    f"{msg.get('line', '?')}")

            return msg

    def find_rest_files(self, path: str):
        def scan(path):
            with os.scandir(path) as it:
                for entry in it:
                    if entry.is_file() and entry.name.endswith('.rst'):
                        files.append(entry.path)
                    if entry.is_dir():
                        scan(entry.path)

        files = []
        scan(path)
        return files

    def extract_code_blocks(self, source: str, filename: str):
        blocks = []

        parser_class = docutils.parsers.get_parser_class('rst')
        parser = parser_class()

        settings = docutils.frontend.OptionParser(
            components=(parser, )).get_default_values()
        settings.syntax_highlight = 'none'

        min_error_code = 100  # Ignore all errors, we process them manually.
        reporter = self.CustomDocutilsReporter(
            filename, min_error_code, min_error_code)
        document = docutils.nodes.document(settings, reporter, source=filename)
        document.note_source(filename, -1)

        parser.parse(source, document)

        lines = source.split('\n')
        for lineno, line in enumerate(lines, 1):
            if len(line) > self.MAX_LINE_LEN:
                reporter.lint_errors.add(
                    f'Line longer than {self.MAX_LINE_LEN} characters in '
                    f'{filename}, line {lineno}')

        if reporter.lint_errors:
            raise self.RestructuredTextStyleError(
                '\n\nRestructuredText lint errors:\n' +
                '\n'.join(reporter.lint_errors))

        directives = document.traverse(
            condition=lambda node: (node.tagname == 'literal_block' and
                                    'code' in node.attributes['classes']))

        for directive in directives:
            classes = directive.attributes['classes']

            if len(classes) < 2 or classes[0] != 'code':
                continue

            lang = directive.attributes['classes'][1]
            code = directive.astext()

            lineno = directive.line
            if lineno is None:
                # Some docutils blocks (like tables) do not support
                # line numbers, so we try to traverse the parent tree
                # to find the nearest block with a line number.
                parent_directive = directive
                while parent_directive and parent_directive.line is None:
                    parent_directive = parent_directive.parent
                if parent_directive and parent_directive.line is not None:
                    lineno = str(parent_directive.line)
            else:
                lineno = str(lineno)

            blocks.append(self.CodeSnippet(filename, lineno, lang, code))

        return blocks

    def run_block_test(self, block):
        try:
            if block.lang == 'eql':
                edgeql_parser.parse_block(block.code)
            elif block.lang == 'eschema':
                schema_parser.parse(block.code)
            elif block.lang == 'pseudo-eql':
                # Skip "pseudo-eql" language as we don't have a parser for it.
                pass
            elif block.lang == 'graphql':
                graphql_parser.parse(block.code)
            else:
                raise LookupError(f'unknown code-block lang {block.lang}')
        except Exception as ex:
            raise AssertionError(
                f'unable to parse {block.lang} code block in '
                f'{block.filename}, around line {block.lineno}') from ex

    def test_doc_snippets(self):
        edgepath = list(edgedb.__path__)[0]
        edgepath = os.path.dirname(edgepath)
        docspath = os.path.join(edgepath, 'doc')

        for filename in self.find_rest_files(docspath):
            with open(filename, 'rt') as f:
                source = f.read()

            blocks = self.extract_code_blocks(source, filename)

            for block in blocks:
                self.run_block_test(block)

    def test_doc_test_broken_code_block(self):
        source = '''
        In large applications, the schema will usually be split
        into several :ref:`modules<ref_schema_evolution_modules>`.

        .. code-block:: eql

            SELECT 122 + foo();

        A *schema module* defines the effective namespace for
        elements it defines.

        .. code-block:: eql

            SELECT 42;
            # ^ expected to return 42
            SELECT foo(

        Schema modules can import other modules to use schema
        elements they define.
        '''

        blocks = self.extract_code_blocks(source, '<test>')
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0].code, 'SELECT 122 + foo();')
        self.run_block_test(blocks[0])

        with self.assertRaisesRegex(AssertionError, 'unable to parse eql'):
            self.run_block_test(blocks[1])

    def test_doc_test_broken_long_lines(self):
        source = f'''
        aaaaaa aa aaa:
        - aaa
        - {'a' * self.MAX_LINE_LEN}
        - aaa
        '''

        with self.assertRaisesRegex(self.RestructuredTextStyleError,
                                    r'lint errors:[.\s]*Line longer'):
            self.extract_code_blocks(source, '<test>')

    def test_doc_test_bad_header(self):
        source = textwrap.dedent('''
            Section
            -----

            aaa aaa aaa
        ''')

        with self.assertRaisesRegex(
                self.RestructuredTextStyleError,
                r'lint errors:[.\s]*Title underline too short'):
            self.extract_code_blocks(source, '<test>')