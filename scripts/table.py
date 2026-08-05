"""Dependency-free helpers for aligned terminal tables."""


def normalize_rows(headers, rows):
    header_values = tuple(str(value) for value in headers)
    normalized = [header_values]
    for row in rows:
        values = tuple(str(value) for value in row)
        if len(values) != len(header_values):
            raise ValueError("table row has a different number of columns")
        normalized.append(values)
    return normalized


def column_widths(headers, rows):
    values = normalize_rows(headers, rows)
    return [max(len(row[index]) for row in values) for index in range(len(headers))]


def format_row(row, widths, indent="", gap="  "):
    values = tuple(str(value) for value in row)
    if len(values) != len(widths):
        raise ValueError("table row has a different number of columns")
    cells = [value.ljust(widths[index]) for index, value in enumerate(values)]
    return indent + gap.join(cells).rstrip()


def format_table(headers, rows, indent="  ", gap="  "):
    widths = column_widths(headers, rows)
    values = normalize_rows(headers, rows)
    return [format_row(row, widths, indent=indent, gap=gap) for row in values]
