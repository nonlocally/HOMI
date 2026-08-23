#!/usr/bin/env python3
"""Do the phone skill and the phone CLI still agree?

A manual that teaches verbs the CLI does not have is worse than no manual:
the agent tries them, they fail, and it learns to distrust the whole
document. The CLI has moved several times; this is what keeps the two
honest as it keeps moving.

Prints "agree", or the verbs the skill invents. Exits non-zero on mismatch.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(HERE, "docs", "phone-skill.md")
CLI = os.path.join(HERE, "lib", "phone")

# Words that follow "phone" in prose but are not verbs.
NOT_VERBS = {"itself", "is", "and", "or", "the", "log", "verbs"}


def documented():
    text = open(DOC, encoding="utf-8").read()
    # Skip the YAML frontmatter: its description is prose about the phone,
    # not instruction about verbs ("drive an Android phone as an agent"
    # otherwise reads as a verb called "as").
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            text = text[end + 4:]
    found = set()
    # code-block lines and inline mentions both count as teaching a verb
    for m in re.finditer(r"(?:^|`|\s)phone ([a-z][a-z-]*)", text, re.M):
        found.add(m.group(1))
    return {v for v in found if v not in NOT_VERBS}


def implemented():
    out = subprocess.run([sys.executable, CLI, "--help"],
                         capture_output=True, text=True).stdout
    known = set()
    # argparse lists subcommands inside {a,b,c}
    for grp in re.findall(r"\{([a-z,\-]+)\}", out):
        known |= set(grp.split(","))
    # ...and again, indented, one per line with its help text
    for m in re.finditer(r"^\s{2,}([a-z][a-z-]*)\s{2,}\S", out, re.M):
        known.add(m.group(1))
    return known


def main():
    doc, cli = documented(), implemented()
    if not cli:
        print("could not read the CLI's verbs")
        return 2
    missing = sorted(doc - cli)
    if missing:
        print("skill teaches verbs the CLI lacks: %s" % ", ".join(missing))
        return 1
    print("agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
