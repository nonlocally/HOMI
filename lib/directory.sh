# shellcheck shell=bash
# communicate :: directory — the shared, committed agent directory. The router
# maps name -> socket (how to reach, live); the registry/ folder maps
# name -> capability (who they are, what they know), one markdown file per agent
# with YAML frontmatter. This command reads registry/, cross-references the live
# router, and shows one unified view (or exports it as JSON).

_registry_dir() { printf '%s/registry\n' "$COMM_HOME"; }

# Parse the registry into JSON on stdout: [{name,kind,model,device,operator,
# updated,availability,reach[],file,live}]. `live` is true if a local sidecar
# currently advertises that name (i.e. the router can reach it right now).
_directory_parse() {
  comm_need_python
  python3 -c '
import sys, os, glob, json
regdir, sessdir = sys.argv[1], sys.argv[2]

# names currently reachable via the router (local sidecars)
live = set()
for f in glob.glob(os.path.join(sessdir, "*.json")):
    try: r = json.load(open(f))
    except Exception: continue
    if r.get("name"): live.add(r["name"])

def parse_fm(path):
    txt = open(path, encoding="utf-8", errors="replace").read()
    if not txt.startswith("---"): return None
    end = txt.find("\n---", 3)
    if end < 0: return None
    d, key = {}, None
    for line in txt[3:end].split("\n"):
        if not line.strip(): continue
        if (line.startswith("  - ") or line.startswith("- ")) and key:
            d.setdefault(key, [])
            if isinstance(d[key], list): d[key].append(line.split("- ", 1)[1].strip().strip(chr(34)))
            continue
        if ":" in line and not line.startswith(" "):
            k, _, v = line.partition(":")
            k, v = k.strip(), v.strip()
            if v == "": d[k] = []; key = k
            else: d[k] = v.strip(chr(34)); key = None
    return d

out = []
for f in sorted(glob.glob(os.path.join(regdir, "*.md"))):
    if os.path.basename(f).lower() == "readme.md": continue
    fm = parse_fm(f)
    if not fm or not fm.get("name"): continue
    fm["file"] = os.path.relpath(f, os.path.dirname(regdir))
    fm["live"] = fm["name"] in live
    out.append(fm)
json.dump(out, sys.stdout, indent=2)
' "$(_registry_dir)" "$(comm_sessions_dir)"
}

# communicate directory [--json]
directory_show() {
  local reg; reg="$(_registry_dir)"
  [ -d "$reg" ] || die "no registry/ directory at $reg"
  if [ "${1:-}" = "--json" ]; then _directory_parse; echo; return 0; fi
  _directory_parse | python3 -c '
import sys, json
rows = json.load(sys.stdin)
if not rows:
    print("  (registry is empty — add registry/<peer-name>.md)"); raise SystemExit
print("  %-38s %-11s %-13s %-11s %-6s %s" % ("NAME","KIND","OPERATOR","DEVICE","LIVE","UPDATED"))
for r in rows:
    print("  %-38s %-11s %-13s %-11s %-6s %s" % (
        (r.get("name") or "?")[:38],
        (r.get("kind") or "?")[:11],
        (r.get("operator") or "?")[:13],
        (r.get("device") or "?")[:11],
        "yes" if r.get("live") else "–",
        r.get("updated") or "?"))
print()
print("  %d agent(s) in the registry; LIVE = reachable via the router right now." % len(rows))
'
}
