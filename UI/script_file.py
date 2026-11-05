"""The generated script as one file, from the start of the program to its end
(generated_scripts/<session>.py): at the start, the script is only an instruction to run
the rules generator once (requirement.md), and so is the file. Each time the script in
place is another one (a run publishes one, the pace makes it definitive further, the
user's changes are applied), the file is written again with it (script_python.to_python);
when the user saves the file with other text than the program wrote, that text is the
script the window applies (Session.edit_script). The program runs the script the file
builds: running the file only builds that object (its SCRIPT).

The file is written whole at once (a temporary file renamed onto it): an editor, or the
window reading it back, never sees half of it. An editor that has it open without
unsaved changes shows the new text by itself (VS Code); with unsaved changes, it keeps
them, and saving them replaces the script then in place.
"""

from __future__ import annotations

import os
import sys

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GENERATED = os.path.join(PROJECT, "generated_scripts")  # (not in git)


class ScriptFile:
    def __init__(self, name: str | None = None, folder: str = GENERATED) -> None:
        name = name or os.path.splitext(os.path.basename(sys.argv[0]))[0] or "session"
        self.path = os.path.join(folder, f"{name}.py")
        self.written: str | None = None  # the text the program wrote last, or the user saved
        self.script = None  # the script the file holds: the one the program wrote it from

    def write(self, script, text: str) -> bool:
        """The file holds `script` (its Python, `text`); written again only if it is another
        script than the one it holds. Whether it was written."""
        if script is self.script:
            return False
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, self.path)
        self.written, self.script = text, script
        return True

    def saved(self) -> str | None:
        """The text the user saved since, if it is not what the program wrote (or what was
        read last): to apply, from `self.script`. None while nothing new is there."""
        try:
            with open(self.path, encoding="utf-8-sig") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError):
            return None  # (being saved, gone, or not text: looked at again later)
        if not text.strip() or text.strip() == (self.written or "").strip():
            return None
        self.written = text
        return text
