"""Docstring popup for IPython terminal prompt.

Displays function signature and docstring preview in a floating popup
when typing or navigating within call parentheses in the terminal.
"""
from __future__ import annotations

import asyncio
import re
import threading
from typing import TYPE_CHECKING, Any

from prompt_toolkit.enums import DEFAULT_BUFFER
from prompt_toolkit.filters import Condition, has_completions, has_focus, is_done
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    Float,
    FloatContainer,
    Window,
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import Frame

if TYPE_CHECKING:
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.shortcuts import PromptSession
    from IPython.terminal.interactiveshell import TerminalInteractiveShell


def _get_jedi():
    """Import and return jedi module lazily."""
    try:
        from IPython.core.completer import _get_jedi as ipy_get_jedi
        return ipy_get_jedi()
    except Exception:
        import jedi
        return jedi


def has_unclosed_parenthesis(text: str) -> bool:
    """Check if there is an unclosed '(' before the cursor."""
    if "(" not in text:
        return False
    in_single_quote = False
    in_double_quote = False
    in_triple_single = False
    in_triple_double = False
    in_comment = False
    paren_depth = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_comment:
            if ch == "\n":
                in_comment = False
            i += 1
            continue

        if in_triple_single:
            if text[i : i + 3] == "'''":
                in_triple_single = False
                i += 3
                continue
            i += 1
            continue

        if in_triple_double:
            if text[i : i + 3] == '"""':
                in_triple_double = False
                i += 3
                continue
            i += 1
            continue

        if in_single_quote:
            if ch == "\\":
                i += 2
                continue
            if ch == "'":
                in_single_quote = False
            i += 1
            continue

        if in_double_quote:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_double_quote = False
            i += 1
            continue

        # Outside strings / comments
        if text[i : i + 3] == "'''":
            in_triple_single = True
            i += 3
            continue
        if text[i : i + 3] == '"""':
            in_triple_double = True
            i += 3
            continue
        if ch == "'":
            in_single_quote = True
            i += 1
            continue
        if ch == '"':
            in_double_quote = True
            i += 1
            continue
        if ch == "#":
            in_comment = True
            i += 1
            continue

        if ch == "(":
            paren_depth += 1
        elif ch == ")":
            if paren_depth > 0:
                paren_depth -= 1
        i += 1

    return paren_depth > 0


def clean_docstring(doc: str | None, name: str | None) -> str:
    """Clean up a docstring for display in the popup tooltip."""
    if not doc:
        return ""
    lines = doc.split("\n")
    # Remove leading lines that repeat the function name or call signature
    while lines and (
        (name and lines[0].strip().startswith(f"{name}("))
        or lines[0].strip().startswith("Signature:")
        or not lines[0].strip()
    ):
        lines.pop(0)
    return "\n".join(lines).strip()


def extract_signature_and_docstring(
    text: str,
    cursor_position: int,
    namespaces: list[dict[str, Any]],
    max_lines: int = 12,
    inspector: Any | None = None,
) -> FormattedText | None:
    """Extract signature and docstring for the callable at cursor position."""
    text_before = text[:cursor_position]
    if not has_unclosed_parenthesis(text_before):
        return None

    lines = text_before.split("\n")
    line = len(lines)
    col = len(lines[-1])

    # 1. Try Jedi
    try:
        jedi_mod = _get_jedi()
        interpreter = jedi_mod.Interpreter(text_before, namespaces)
        sigs = interpreter.get_signatures(line=line, column=col)
        if sigs:
            sig = sigs[0]
            sig_name = sig.name
            if sig_name in ("<lambda>", "<anonymous>"):
                match = re.search(r"([a-zA-Z_][a-zA-Z0-9_\.]*)\s*\([^()]*$", text_before)
                if match:
                    sig_name = match.group(1).split(".")[-1]

            fragments: list[tuple[str, str]] = []
            fragments.append(("class:docstring-popup.name", sig_name))
            fragments.append(("class:docstring-popup.punctuation", "("))

            for i, p in enumerate(sig.params):
                if i > 0:
                    fragments.append(("class:docstring-popup.punctuation", ", "))
                p_str = p.to_string() if hasattr(p, "to_string") else p.name
                if i == sig.index:
                    fragments.append(("class:docstring-popup.active-param", p_str))
                else:
                    fragments.append(("class:docstring-popup.param", p_str))

            fragments.append(("class:docstring-popup.punctuation", ")"))

            to_str = sig.to_string()
            if " -> " in to_str:
                ret = to_str[to_str.index(" -> ") :]
                fragments.append(("class:docstring-popup.return", ret))

            doc = sig.docstring()
            cleaned_doc = clean_docstring(doc, sig.name)
            if cleaned_doc:
                fragments.append(("", "\n"))
                doc_lines = cleaned_doc.split("\n")
                if len(doc_lines) > max_lines:
                    truncated = "\n".join(doc_lines[:max_lines]) + "\n..."
                else:
                    truncated = "\n".join(doc_lines)
                fragments.append(("class:docstring-popup.doc", truncated))

            return FormattedText(fragments)
    except Exception:
        pass

    # 2. Fallback to inspector if Jedi didn't return a signature
    if inspector is not None:
        try:
            # Find the symbol name right before the opening parenthesis
            match = re.search(r"([a-zA-Z_][a-zA-Z0-9_\.]*)\s*\([^()]*$", text_before)
            if match:
                symbol_name = match.group(1)
                obj = None
                for ns in namespaces:
                    if symbol_name in ns:
                        obj = ns[symbol_name]
                        break
                    elif "." in symbol_name:
                        parts = symbol_name.split(".")
                        if parts[0] in ns:
                            curr = ns[parts[0]]
                            for p in parts[1:]:
                                curr = getattr(curr, p, None)
                                if curr is None:
                                    break
                            if curr is not None:
                                obj = curr
                                break
                if obj is not None and callable(obj):
                    info = inspector.info(obj, oname=symbol_name)
                    if info.get("found"):
                        definition = info.get("definition") or info.get("call_def") or symbol_name + "(...)"
                        doc = info.get("docstring") or ""
                        fragments = [
                            ("class:docstring-popup.signature", definition),
                        ]
                        cleaned_doc = clean_docstring(doc, symbol_name)
                        if cleaned_doc:
                            fragments.append(("", "\n"))
                            doc_lines = cleaned_doc.split("\n")
                            if len(doc_lines) > max_lines:
                                truncated = "\n".join(doc_lines[:max_lines]) + "\n..."
                            else:
                                truncated = "\n".join(doc_lines)
                            fragments.append(("class:docstring-popup.doc", truncated))
                        return FormattedText(fragments)
        except Exception:
            pass

    return None


class DocstringPopupManager:
    """Manages the lifecycle, fetching, and display of docstring tooltips in prompt_toolkit."""

    def __init__(self, shell: TerminalInteractiveShell) -> None:
        self.shell = shell
        self.session: PromptSession | None = None
        self._current_formatted_text: FormattedText | None = None
        self._last_evaluated: tuple[str, int] | None = None
        self._pending_timer: Any | None = None
        self._popup_float: Float | None = None

    def get_formatted_text(self) -> FormattedText:
        """Return the formatted text to be displayed in the popup."""
        return self._current_formatted_text or FormattedText([])

    def is_visible(self) -> bool:
        """Whether the docstring popup is currently active with content."""
        if not getattr(self.shell, "docstring_popup", True):
            return False
        if not self._current_formatted_text or len(self._current_formatted_text) == 0:
            return False
        if self.session is not None:
            try:
                doc = self.session.default_buffer.document
                if not doc.text or not has_unclosed_parenthesis(doc.text[:doc.cursor_position]):
                    return False
            except Exception:
                pass
        return True

    def clear(self) -> None:
        """Clear current popup content and cancel any scheduled update."""
        self._cancel_timer()
        self._current_formatted_text = None
        self._last_evaluated = None

    def _cancel_timer(self) -> None:
        """Cancel pending timer if any."""
        if self._pending_timer is not None:
            try:
                if hasattr(self._pending_timer, "cancel"):
                    self._pending_timer.cancel()
            except Exception:
                pass
            self._pending_timer = None

    def _get_namespaces(self) -> list[dict[str, Any]]:
        """Get the current execution namespaces for inspection."""
        namespaces = []
        if hasattr(self.shell, "user_ns") and self.shell.user_ns is not None:
            namespaces.append(self.shell.user_ns)
        if hasattr(self.shell, "user_global_ns") and self.shell.user_global_ns is not None:
            namespaces.append(self.shell.user_global_ns)
        return namespaces

    def update_docstring(self, text: str, cursor_position: int) -> None:
        """Evaluate and update docstring popup content synchronously."""
        self._last_evaluated = (text, cursor_position)
        inspector = getattr(self.shell, "inspector", None)
        max_lines = getattr(self.shell, "docstring_popup_max_lines", 12)
        namespaces = self._get_namespaces()
        self._current_formatted_text = extract_signature_and_docstring(
            text,
            cursor_position,
            namespaces,
            max_lines=max_lines,
            inspector=inspector,
        )

    def on_buffer_changed(self, buffer: Buffer) -> None:
        """Handler for buffer text and cursor position changes."""
        if not getattr(self.shell, "docstring_popup", True):
            self.clear()
            return

        document = buffer.document
        text = document.text
        cursor_position = document.cursor_position
        text_before = text[:cursor_position]

        if (text, cursor_position) == self._last_evaluated:
            return

        self._cancel_timer()

        # If not currently inside an unclosed call parenthesis, clear immediately
        if not has_unclosed_parenthesis(text_before):
            self.clear()
            self._last_evaluated = (text, cursor_position)
            self._request_repaint()
            return

        delay = getattr(self.shell, "docstring_popup_delay", 0.2)
        if delay <= 0:
            self.update_docstring(text, cursor_position)
            self._request_repaint()
        else:
            self._schedule_update(text, cursor_position, delay)

    def _schedule_update(self, text: str, cursor_position: int, delay: float) -> None:
        """Schedule an update after the given delay."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            self._pending_timer = loop.call_later(
                delay,
                self._on_timer_fired,
                text,
                cursor_position,
            )
        else:
            timer = threading.Timer(
                delay,
                self._on_timer_fired,
                args=(text, cursor_position),
            )
            timer.daemon = True
            timer.start()
            self._pending_timer = timer

    def _on_timer_fired(self, text: str, cursor_position: int) -> None:
        """Timer callback when debounce delay expires."""
        if self.session is not None:
            try:
                buf = self.session.default_buffer
                if buf.document.text != text or buf.document.cursor_position != cursor_position:
                    return
            except Exception:
                pass

        self.update_docstring(text, cursor_position)
        self._request_repaint()

    def _request_repaint(self) -> None:
        """Request UI redraw from prompt_toolkit application."""
        if self.session is not None and hasattr(self.session, "app"):
            try:
                self.session.app.invalidate()
            except Exception:
                pass

    def create_float(self) -> Float:
        """Create the Float widget for the docstring popup."""
        content_window = Window(
            content=FormattedTextControl(self.get_formatted_text),
            wrap_lines=False,
            dont_extend_width=True,
            dont_extend_height=True,
            style="class:docstring-popup",
        )

        frame = Frame(content_window, style="class:docstring-popup.frame")

        visible_filter = (
            Condition(self.is_visible)
            & has_focus(DEFAULT_BUFFER)
            & ~is_done
            & ~has_completions
        )

        return Float(
            xcursor=True,
            ycursor=True,
            allow_cover_cursor=False,
            content=ConditionalContainer(
                content=frame,
                filter=visible_filter,
            ),
        )

    def attach_to_session(self, session: PromptSession) -> None:
        """Attach event handlers and layout float to PromptSession."""
        self.session = session
        buf = session.default_buffer

        buf.on_text_changed += self.on_buffer_changed
        buf.on_cursor_position_changed += self.on_buffer_changed

        if self._popup_float is None:
            self._popup_float = self.create_float()

        # Find FloatContainers in the layout hierarchy and add the popup float
        self._inject_float(session.layout.container)

    def _inject_float(self, container: Any) -> None:
        """Recursively locate FloatContainer instances and attach popup float."""
        if isinstance(container, FloatContainer):
            if self._popup_float not in container.floats:
                container.floats.append(self._popup_float)
        if hasattr(container, "get_children"):
            for child in container.get_children():
                self._inject_float(child)
