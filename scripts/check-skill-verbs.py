#!/usr/bin/env python3
"""Do the phone skill and the phone CLI still agree?

A manual that teaches verbs the CLI does not have is worse than no manual:
the agent tries them, they fail, and it learns to distrust the whole
document. The CLI has moved several times; this is what keeps the two
honest as it keeps moving.

Prints "agree", or the verbs the skill invents. Exits non-zero on mismatch.
"""
import glob
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Every CLI a skill is allowed to teach, longest name FIRST so that
# "phone-pay mint" is not read as the `phone` verb "pay". A skill that teaches
# a second binary drifts from it exactly as easily as from the first, and
# `pay` was unchecked entirely until this list existed.
CLIS = [
    ("phone-pay", os.path.join(HERE, "lib", "phone-pay")),
    ("phone", os.path.join(HERE, "lib", "phone")),
]
# EVERY skill that teaches `phone`, not just the general one. An app skill
# invents verbs just as easily as the charter does, and a per-app manual is
# read by an agent that has no reason to doubt it.
DOCS = sorted(glob.glob(os.path.join(HERE, "plugins", "*", "skills", "*",
                                     "SKILL.md")))

# Words that follow "phone" in prose but are not verbs.
NOT_VERBS = {"itself", "is", "and", "or", "the", "log", "verbs"}


def documented(doc):
    text = open(doc, encoding="utf-8").read()
    # Skip the YAML frontmatter: its description is prose about the phone,
    # not instruction about verbs ("drive an Android phone as an agent"
    # otherwise reads as a verb called "as").
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            text = text[end + 4:]
    # Only CODE teaches a verb. Prose says things like "address the phone
    # with one flag", and chasing those with an ever-growing exception list
    # is how a check stops meaning anything. Fenced blocks and inline code
    # are where the instructions actually live.
    blocks = re.findall(r"```(.*?)```", text, re.S)
    inline = re.findall(r"`([^`\n]+)`", text)
    found = set()
    for chunk in blocks + inline:
        for name, _path in CLIS:
            pat = r"(?:^|\s)%s (?:--\S+ \S+ )*([a-z][a-z-]*)" % re.escape(name)
            for m in re.finditer(pat, chunk, re.M):
                found.add((name, m.group(1)))
            # Blank out what matched, so `phone-pay fill` is not then re-read
            # by the shorter `phone` pattern on the same text.
            chunk = re.sub(r"(?:^|\s)%s\b" % re.escape(name), " ", chunk)
    return {(c, v) for (c, v) in found if v not in NOT_VERBS}


def implemented_one(path):
    out = subprocess.run([sys.executable, path, "--help"],
                         capture_output=True, text=True).stdout
    known = set()
    # argparse lists subcommands inside {a,b,c}
    for grp in re.findall(r"\{([a-z,\-]+)\}", out):
        known |= set(grp.split(","))
    # ...and again, indented, one per line with its help text
    for m in re.finditer(r"^\s{2,}([a-z][a-z-]*)\s{2,}\S", out, re.M):
        known.add(m.group(1))
    return known


def implemented():
    out = set()
    for name, path in CLIS:
        if not os.path.exists(path):
            continue
        out |= {(name, v) for v in implemented_one(path)}
    return out


def main():
    cli = implemented()
    if not cli:
        print("could not read the CLI's verbs")
        return 2
    if not DOCS:
        print("no skill documents found")
        return 2
    bad = 0
    for doc in DOCS:
        name = os.path.basename(os.path.dirname(doc))
        missing = sorted(documented(doc) - cli)
        if missing:
            print("%s teaches verbs the CLI lacks: %s"
                  % (name, ", ".join("%s %s" % cv for cv in missing)))
            bad = 1
    if bad:
        return 1
    print("agree (%s)"
          % ", ".join(os.path.basename(os.path.dirname(d))
                      for d in DOCS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
