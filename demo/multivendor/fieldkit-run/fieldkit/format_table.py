"""Format rows as a plain text table with padded columns."""


def format_table(rows: list[list[str]]) -> str:
    """Format rows into a plain text table with padded columns."""
    if not rows:
        return ""

    num_cols = max(len(row) for row in rows)
    col_widths = [0] * num_cols

    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    formatted_rows = []
    for row in rows:
        formatted_cells = []
        for i in range(num_cols):
            cell = row[i] if i < len(row) else ""
            formatted_cells.append(cell.ljust(col_widths[i]))
        formatted_rows.append("  ".join(formatted_cells))

    return "\n".join(formatted_rows)
