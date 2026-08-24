Display function signature and docstring popup
------------------------------------------------

IPython now supports displaying a function's signature and docstring in a floating
tooltip popup when typing or navigating inside call parentheses in the terminal prompt.

The active parameter is highlighted automatically based on the cursor position within the call.

This feature is configurable via traitlets:

- ``TerminalInteractiveShell.docstring_popup = True`` (enabled by default)
- ``TerminalInteractiveShell.docstring_popup_delay = 0.2`` (delay in seconds before popup appears)
- ``TerminalInteractiveShell.docstring_popup_max_lines = 12`` (maximum number of lines displayed in the popup)
