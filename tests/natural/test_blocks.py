import textwrap

from nighthawk.natural.blocks import find_natural_blocks, is_natural_sentinel


def test_docstring_natural_block_detected_and_bindings_extracted():
    src = textwrap.dedent(
        '''
        def f(numbers):
            """natural
            Consider <numbers> and compute <:result>.
            """
            return result
        '''
    )
    blocks = find_natural_blocks(src)
    assert len(blocks) == 1
    b = blocks[0]
    assert b.kind == "docstring"
    assert b.input_bindings == ("numbers",)
    assert b.bindings == ("result",)
    assert b.text.splitlines()[0] == "Consider <numbers> and compute <:result>."


def test_inline_natural_block_detected():
    src = textwrap.dedent(
        '''
        def f(x):
            """not natural"""
            """natural
            Make <:y> be 1.
            """
            return y
        '''
    )
    blocks = find_natural_blocks(src)
    assert len(blocks) == 1
    assert blocks[0].kind == "inline"
    assert blocks[0].bindings == ("y",)
    assert blocks[0].text.splitlines()[0] == "Make <:y> be 1."


def test_inline_fstring_natural_block_detected_and_bindings_extracted():
    src = textwrap.dedent(
        '''
        def f(x):
            f"""natural
            Set <:y> to {x + 1}.
            """
            return y
        '''
    )
    blocks = find_natural_blocks(src)
    assert len(blocks) == 1
    assert blocks[0].kind == "inline"
    assert blocks[0].input_bindings == ()
    assert blocks[0].bindings == ("y",)


def test_inline_ast_shape_must_be_literal_or_fstring():
    src = textwrap.dedent(
        """
        def f(x):
            ("natural\\nMake <:y> be 1.\\n").format(x=x)
            return y
        """
    )
    blocks = find_natural_blocks(src)
    assert blocks == ()


def test_natural_sentinel_rejects_leading_blank_line_docstring():
    src = textwrap.dedent(
        '''
        def f():
            """
            natural
            Make <:y> be 1.
            """
            return y
        '''
    )
    blocks = find_natural_blocks(src)
    assert blocks == ()


def test_natural_sentinel_rejects_leading_whitespace_docstring():
    src = textwrap.dedent(
        '''
        def f():
            """ natural
            Make <:y> be 1.
            """
            return y
        '''
    )
    blocks = find_natural_blocks(src)
    assert blocks == ()


def test_natural_sentinel_rejects_trailing_whitespace_on_sentinel_line_docstring():
    src = textwrap.dedent(
        '''
        def f():
            """natural\t
            Make <:y> be 1.
            """
            return y
        '''
    )
    blocks = find_natural_blocks(src)
    assert blocks == ()


def test_is_natural_sentinel_rejects_trailing_whitespace_line():
    assert is_natural_sentinel("natural \nrest") is False


def test_natural_inline_parentheses_do_not_matter():
    src = textwrap.dedent(
        '''
        def f():
            x = 0
            ("""natural
            Make <:y> be 1.
            """)
            _ = x
            return y
        '''
    )
    blocks = find_natural_blocks(src)
    assert len(blocks) == 1
    assert blocks[0].kind == "inline"
    assert blocks[0].bindings == ("y",)
    assert blocks[0].text.splitlines()[0] == "Make <:y> be 1."
