"""Aria Code — UI package.

Public surface:

    from ui import console, HAS_RICH, HAS_PT        # shared console + flags
    from ui import _EscWatcher, _esc_watcher         # ESC cancellation
    from ui import AriaPTCompleter, ARIA_PT_STYLE    # prompt_toolkit completer
    from ui import arrow_select, run_picker_in_thread # arrow-key picker
    from ui import PanelInputConfig, run_panel_input  # structured input panel
    from ui.render import render_verdict_banner, ...  # Rich output renderers
    from ui.banner import render_full_banner, ...     # startup banner
    from ui.render.output import print_error, print_tool_result, ...
"""

from .console import (
    console,
    HAS_RICH,
    HAS_PT,
    _SYNTAX_THEME,
    _EscWatcher,
    _esc_watcher,
    _HAS_TERMIOS,
)

from .completer import AriaPTCompleter, ARIA_PT_STYLE

from .picker import (
    arrow_select,
    run_picker_in_thread,
    _arrow_select,
    _run_picker_in_thread,
)

from .input_box import (
    PanelInputConfig,
    run_panel_input,
    detect_terminal_theme,
    PromptAndPlaceholderProcessor,
    PlaceholderProcessor,
)

from .robot import (
    RobotState,
    set_robot_state,
    get_robot_state,
    get_robot_row,
    get_robot_frame,
    get_status_dot,
)

from .banner import (
    privacy_status_label,
    control_status_label,
    ollama_status_label,
    bottom_toolbar_parts,
    render_compact_banner,
    render_full_banner,
    render_try_hints,
)

from .render.output import (
    FINANCE_TOOL_NAMES,
    clean_tool_error_message,
    error_hint,
    print_error,
    print_tool_result,
    print_tool_activity_group,
    print_fallback_toast,
    print_context_warning,
    print_tool_blocked,
    print_thinking_header,
    print_done_footer,
)

__all__ = [
    # console
    "console", "HAS_RICH", "HAS_PT", "_SYNTAX_THEME",
    "_EscWatcher", "_esc_watcher", "_HAS_TERMIOS",
    # completer
    "AriaPTCompleter", "ARIA_PT_STYLE",
    # picker
    "arrow_select", "run_picker_in_thread",
    "_arrow_select", "_run_picker_in_thread",
    # input panel
    "PanelInputConfig", "run_panel_input",
    "detect_terminal_theme",
    "PromptAndPlaceholderProcessor", "PlaceholderProcessor",
    # robot mascot
    "RobotState", "set_robot_state", "get_robot_state",
    "get_robot_row", "get_robot_frame", "get_status_dot",
    # banner
    "privacy_status_label", "control_status_label", "ollama_status_label",
    "bottom_toolbar_parts", "render_compact_banner", "render_full_banner", "render_try_hints",
    # output rendering
    "FINANCE_TOOL_NAMES", "clean_tool_error_message", "error_hint",
    "print_error", "print_tool_result",
    "print_tool_activity_group", "print_fallback_toast",
    "print_context_warning", "print_tool_blocked",
    "print_thinking_header", "print_done_footer",
]
