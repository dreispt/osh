"""Backup file format detection by content inspection."""


def detect_backup_format_by_content(path):
    """Detect backup format by inspecting file content headers.

    Returns one of: 'dump', 'sql', 'sql.gz', 'zip', or None if unknown.
    """
    try:
        with open(path, "rb") as f:
            header = f.read(16)  # Read first 16 bytes for magic number detection

            # Check for ZIP format (PK header)
            if header.startswith(b"PK"):
                return "zip"

            # Check for GZIP format
            if header.startswith(b"\x1f\x8b"):
                return "sql.gz"

            # Check for PostgreSQL custom format (pg_dump -Fc)
            # PostgreSQL custom format starts with "PGDMP" followed by version info
            if header[:5] == b"PGDMP":
                return "dump"

            # Check for plain SQL by looking at first few bytes
            # SQL files typically start with comments, SET commands, or CREATE statements
            if header:
                text_sample = header.decode("utf-8", errors="ignore").lower()
                sql_keywords = [
                    b"set ",
                    b"create",
                    b"alter",
                    b"drop",
                    b"insert",
                    b"update",
                    b"delete",
                    b"select",
                    b"begin",
                    b"commit",
                    b"--",
                    b"/*",
                ]
                if any(keyword in text_sample.encode() for keyword in sql_keywords):
                    return "sql"

                # Also check if it's mostly printable ASCII characters (likely SQL)
                if all(32 <= byte <= 126 or byte in [9, 10, 13, 32] for byte in header):
                    return "sql"
    except (OSError, UnicodeDecodeError):
        pass

    return None
