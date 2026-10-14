import csv
import io

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_ROWS = 500


class SpreadsheetError(Exception):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def read_table(uploaded, required_columns, optional_columns=()):
    if uploaded is None:
        raise SpreadsheetError(["Choose a CSV file to import."])
    name = (getattr(uploaded, "name", "") or "").lower()
    if not name.endswith(".csv"):
        raise SpreadsheetError(["Import a .csv file. Other spreadsheet formats are not accepted."])
    size = getattr(uploaded, "size", None)
    if size is not None and size > MAX_UPLOAD_BYTES:
        raise SpreadsheetError(["The file is larger than 2 MB."])
    raw = uploaded.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise SpreadsheetError(["The file is larger than 2 MB."])
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SpreadsheetError(["The file must be UTF-8 text."]) from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise SpreadsheetError(["The file has no header row."])
    headers = [header.strip() for header in reader.fieldnames]
    if any(header == "" or header is None for header in reader.fieldnames):
        raise SpreadsheetError(["The header row has an empty column name."])
    missing = [column for column in required_columns if column not in headers]
    unknown = [column for column in headers if column not in required_columns and column not in optional_columns]
    errors = []
    if missing:
        errors.append("Missing columns: " + ", ".join(missing) + ".")
    if unknown:
        errors.append("Unknown columns: " + ", ".join(unknown) + ".")
    if errors:
        raise SpreadsheetError(errors)
    rows = []
    for index, row in enumerate(reader, start=2):
        cleaned = {column: (row.get(column) or "").strip() for column in required_columns}
        for column in optional_columns:
            cleaned[column] = (row.get(column) or "").strip()
        if not any(cleaned.values()):
            continue
        rows.append((index, cleaned))
        if len(rows) > MAX_ROWS:
            raise SpreadsheetError([f"Import at most {MAX_ROWS} data rows."])
    if not rows:
        raise SpreadsheetError(["The file has a header but no data rows."])
    return rows


def csv_response(filename, columns, data_rows):
    from django.http import HttpResponse

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(columns)
    for row in data_rows:
        writer.writerow(row)
    return response


def parse_bool(value, *, row_number, column):
    if value == "":
        return False
    token = value.lower()
    if token in {"1", "true", "yes", "y"}:
        return True
    if token in {"0", "false", "no", "n"}:
        return False
    raise ValueError(f"Row {row_number}: {column} must be yes or no.")


def parse_int(value, *, row_number, column):
    if value == "":
        return None
    try:
        number = int(value)
    except ValueError as exc:
        raise ValueError(f"Row {row_number}: {column} must be a whole number.") from exc
    if number < 0:
        raise ValueError(f"Row {row_number}: {column} cannot be negative.")
    return number


def parse_decimal(value, *, row_number, column, max_digits, decimal_places, maximum=None):
    from decimal import Decimal, InvalidOperation

    if value == "":
        return None
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"Row {row_number}: {column} must be a number such as 1500.00.") from exc
    if number < 0:
        raise ValueError(f"Row {row_number}: {column} cannot be negative.")
    _sign, digits, exponent = number.as_tuple()
    if exponent >= 0:
        places = 0
        whole = len(digits) + exponent
    else:
        places = -exponent
        whole = max(0, len(digits) + exponent)
    if places > decimal_places or whole + places > max_digits:
        raise ValueError(
            f"Row {row_number}: {column} must have at most {max_digits - decimal_places} digits before the decimal and {decimal_places} after."
        )
    if maximum is not None and number > maximum:
        raise ValueError(f"Row {row_number}: {column} cannot be greater than {maximum}.")
    return number
