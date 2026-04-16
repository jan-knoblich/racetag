#!/usr/bin/env python3
"""Generate apps/desktop/win_version_info.txt from apps/desktop/VERSION.

Usage:
    python3 generate_win_version_info.py

Reads VERSION (e.g. "0.1.0"), writes win_version_info.txt in the PyInstaller
VSVersionInfo format.  Version tuple is always 4-element: (0, 1, 0, 0).
Run this before building the Windows exe.
"""

from pathlib import Path


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    version_str = (script_dir / "VERSION").read_text().strip()

    parts = version_str.split(".")
    # Pad or truncate to exactly 4 numeric parts.
    while len(parts) < 4:
        parts.append("0")
    version_tuple = tuple(int(p) for p in parts[:4])
    v = version_tuple  # shorter alias

    content = f"""\
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={v},
    prodvers={v},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          u'040904B0',
          [StringStruct(u'CompanyName', u'Racetag'),
           StringStruct(u'FileDescription', u'Racetag'),
           StringStruct(u'FileVersion', u'{version_str}'),
           StringStruct(u'InternalName', u'Racetag'),
           StringStruct(u'LegalCopyright', u'\\xa9 2026 Jan Knoblich'),
           StringStruct(u'OriginalFilename', u'Racetag.exe'),
           StringStruct(u'ProductName', u'Racetag'),
           StringStruct(u'ProductVersion', u'{version_str}')])
      ]
    ),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"""

    output_path = script_dir / "win_version_info.txt"
    output_path.write_text(content, encoding="utf-8")
    print(f"Wrote {output_path} (version {version_str})")


if __name__ == "__main__":
    main()
