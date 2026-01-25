import textwrap

from nighthawk.natural.blocks import find_natural_blocks


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
