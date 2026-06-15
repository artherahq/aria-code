"""Backwards-compatibility shim — canonical code lives in ui/render/team.py."""
from ui.render.team import *  # noqa: F401, F403
from ui.render.team import (
    build_verdict_body, render_verdict_banner,
    TeamTableRow, calc_column_widths, truncate_cell,
    team_mode_label, build_team_table_rows,
    render_team_rows_plain, render_team_table,
)
