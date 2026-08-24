"""Tests for IPython docstring popup feature."""

from typing import Any
import pytest
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.shortcuts import PromptSession

from IPython.core.interactiveshell import InteractiveShell
from IPython.terminal.docstring import (
    DocstringPopupManager,
    clean_docstring,
    extract_signature_and_docstring,
    has_unclosed_parenthesis,
)


class DummyShell:
    def __init__(self):
        self.docstring_popup = True
        self.docstring_popup_delay = 0.0
        self.docstring_popup_max_lines = 12
        self.user_ns: dict[str, Any] = {}
        self.user_global_ns: dict[str, Any] = {}
        self.inspector = None


def test_has_unclosed_parenthesis():
    assert not has_unclosed_parenthesis("")
    assert not has_unclosed_parenthesis("sum")
    assert has_unclosed_parenthesis("sum(")
    assert has_unclosed_parenthesis("sum(10, 20")
    assert not has_unclosed_parenthesis("sum(10, 20)")
    assert not has_unclosed_parenthesis("sum([10, 20])")
    assert has_unclosed_parenthesis("sum([10, 20], ")
    assert not has_unclosed_parenthesis("print('(')")
    assert has_unclosed_parenthesis("print('('")
    assert not has_unclosed_parenthesis("print(\"hello (\")")
    assert not has_unclosed_parenthesis("# comment (")


def test_clean_docstring():
    assert clean_docstring(None, "foo") == ""
    assert clean_docstring("", "foo") == ""
    assert clean_docstring("   ", "foo") == ""

    doc = "foo(x, y)\n\nThis is a docstring."
    assert clean_docstring(doc, "foo") == "This is a docstring."

    doc_sig = "Signature: foo(x, y)\n\nAnother docstring."
    assert clean_docstring(doc_sig, "foo") == "Another docstring."

    normal_doc = "A regular docstring without signature."
    assert clean_docstring(normal_doc, "foo") == "A regular docstring without signature."


def test_extract_signature_no_parenthesis():
    ns: list[dict[str, Any]] = []
    assert extract_signature_and_docstring("print", 5, ns) is None
    assert extract_signature_and_docstring("x = 10", 6, ns) is None


def test_extract_signature_closed_parenthesis():
    ns: list[dict[str, Any]] = []
    assert extract_signature_and_docstring("sum(10, 20)", 12, ns) is None
    assert extract_signature_and_docstring("sum([10, 20])", 13, ns) is None


def test_extract_signature_builtin_print():
    ns: list[dict[str, Any]] = []
    text = "print("
    result = extract_signature_and_docstring(text, len(text), ns)
    assert result is not None
    assert isinstance(result, FormattedText)

    rendered_text = "".join(fragment[1] for fragment in result)
    assert "print(" in rendered_text
    assert "*values" in rendered_text or "values" in rendered_text


def test_extract_signature_custom_function_and_active_param():
    def my_func(first: int, second: str = "default", *args, **kwargs) -> bool:
        """Custom docstring for my_func."""
        return True

    ns = [{"my_func": my_func}]

    # At first argument: 'first' should be active
    text1 = "my_func("
    res1 = extract_signature_and_docstring(text1, len(text1), ns)
    assert res1 is not None
    active_tokens1 = [t[1] for t in res1 if t[0] == "class:docstring-popup.active-param"]
    assert len(active_tokens1) == 1
    assert "first" in active_tokens1[0]

    # At second argument: 'second' should be active
    text2 = "my_func(1, "
    res2 = extract_signature_and_docstring(text2, len(text2), ns)
    assert res2 is not None
    active_tokens2 = [t[1] for t in res2 if t[0] == "class:docstring-popup.active-param"]
    assert len(active_tokens2) == 1
    assert "second" in active_tokens2[0]


def test_extract_signature_nested_call():
    ns: list[dict[str, Any]] = []
    text = "print(len("
    res = extract_signature_and_docstring(text, len(text), ns)
    assert res is not None
    rendered = "".join(fragment[1] for fragment in res)
    assert "len(" in rendered


def test_extract_signature_docstring_truncation():
    def long_doc_func(x):
        """Line 1
        Line 2
        Line 3
        Line 4
        Line 5
        Line 6
        Line 7
        Line 8
        """
        pass

    ns = [{"long_doc_func": long_doc_func}]
    text = "long_doc_func("
    res = extract_signature_and_docstring(text, len(text), ns, max_lines=3)
    assert res is not None
    rendered = "".join(fragment[1] for fragment in res)
    assert "..." in rendered


def test_extract_signature_fallback_inspector():
    shell = InteractiveShell.instance()

    def special_func(alpha, beta=100):
        """Special doc."""
        return alpha + beta

    ns = [{"special_func": special_func}]
    # Using inspector with a text that might fallback
    res = extract_signature_and_docstring(
        "special_func(",
        len("special_func("),
        ns,
        inspector=shell.inspector,
    )
    assert res is not None
    rendered = "".join(fragment[1] for fragment in res)
    assert "special_func" in rendered


def test_popup_manager_visibility_and_clear():
    shell = DummyShell()
    shell.user_ns["square"] = lambda x: x * x
    manager = DocstringPopupManager(shell=shell)

    assert not manager.is_visible()

    # Update with a call
    manager.update_docstring("square(", 7)
    assert manager.is_visible()
    tokens = manager.get_formatted_text()
    assert len(tokens) > 0

    # Clear
    manager.clear()
    assert not manager.is_visible()
    assert len(manager.get_formatted_text()) == 0

    # Disabled via shell setting
    shell.docstring_popup = False
    manager.update_docstring("square(", 7)
    assert not manager.is_visible()


def test_popup_manager_on_buffer_changed():
    shell = DummyShell()
    shell.user_ns["cube"] = lambda n: n * n * n
    manager = DocstringPopupManager(shell=shell)

    buf = Buffer()
    buf.set_document(Document("cube(", 5))
    manager.on_buffer_changed(buf)

    assert manager.is_visible()
    assert "cube" in "".join(t[1] for t in manager.get_formatted_text())

    # Now close parenthesis: buffer changed to cube(10)
    buf.set_document(Document("cube(10)", 8))
    manager.on_buffer_changed(buf)
    assert not manager.is_visible()

    # Empty buffer: new line / prompt
    buf.set_document(Document("", 0))
    manager.on_buffer_changed(buf)
    assert not manager.is_visible()


@pytest.mark.asyncio
async def test_popup_manager_attach_to_session():
    shell = DummyShell()
    manager = DocstringPopupManager(shell=shell)
    session = PromptSession()

    manager.attach_to_session(session)
    assert manager.session is session
    assert manager._popup_top_container is not None
    assert manager._popup_float is not None

    # Test session buffer clearing
    session.default_buffer.set_document(Document("sum(", 4))
    manager.on_buffer_changed(session.default_buffer)
    assert manager.is_visible()

    # When new prompt line is empty, is_visible must be False
    session.default_buffer.set_document(Document("", 0))
    assert not manager.is_visible()
