import nighthawk as nh


def test_decorator_updates_output_binding_via_docstring_natural_block(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    @nh.fn
    def f(x: int):
        """natural
        <:result>
        {{"assignments": [{{"target": "<result>", "expression": "x + 1"}}]}}
        """
        result = 0
        return result

    assert f(10) == 11


def test_decorator_updates_output_binding_via_inline_natural_block(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    @nh.fn
    def f(x: int):
        result = 0
        """natural
        <:result>
        {{"assignments": [{{"target": "<result>", "expression": "x * 2"}}]}}
        """
        return result

    assert f(6) == 12
