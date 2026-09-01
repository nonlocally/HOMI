# shellcheck shell=bash
# communicate :: router — the agent-router. Every reachable agent is a
# name -> socket entry (its sidecar). Routing is: resolve the name, write the
# socket. Local Claude sessions, bridged remote Claude sessions, and Codex peers
# are all just sockets in the same table, so one `route` reaches any of them.

# Print the routing table: every agent currently reachable by name on this host.
router_agents() {
  comm_need_python
  python3 -c '
import sys, json, glob, os
sessions_dir, state_dir, mysock = sys.argv[1:4]

# Classify via our own bookkeeping.
peers = {}      # socket -> (device, name)   [codex peers]
bridged = {}    # remote_pid -> device       [bridged remote claude]
for meta in glob.glob(os.path.join(state_dir, "peers", "*", "meta")):
    d = {}
    for ln in open(meta):
        if "=" in ln: k,v = ln.rstrip("\n").split("=",1); d[k]=v
    if d.get("socket"): peers[d["socket"]] = (d.get("device","?"), d.get("name","?"))
for meta in glob.glob(os.path.join(state_dir, "bridges", "*", "meta")):
    d = {}
    for ln in open(meta):
        if "=" in ln: k,v = ln.rstrip("\n").split("=",1); d[k]=v
    if d.get("remote_pid"): bridged[str(d["remote_pid"])] = d.get("device","?")

rows = []
for f in glob.glob(os.path.join(sessions_dir, "*.json")):
    try: r = json.load(open(f))
    except Exception: continue
    sock = r.get("messagingSocketPath",""); name = r.get("name") or "(unnamed)"
    pid = str(r.get("pid","")); status = r.get("status","?")
    if sock in peers:
        typ, via = "codex", peers[sock][0]
    elif pid in bridged:
        typ, via = "claude*", bridged[pid]
    elif sock == mysock:
        typ, via = "claude", "self"
    else:
        typ, via = "claude", "local"
    rows.append((name, typ, via, status, sock))

rows.sort(key=lambda x:(x[1], x[0]))
if not rows:
    print("  (no reachable agents)"); raise SystemExit
print("  %-24s %-8s %-18s %-8s %s" % ("NAME","TYPE","VIA","STATUS","SOCKET"))
for n,t,v,s,sk in rows:
    print("  %-24s %-8s %-18s %-8s %s" % (n[:24],t,v[:18],s[:8],sk))
print()
print("  claude* = a remote Claude session bridged in over ssh")
' "$(comm_sessions_dir)" "$COMM_STATE" "${CLAUDE_CODE_MESSAGING_SOCKET:-}"
}

# Resolve a name to "type\tvia\tsocket".
router_whereis() {
  comm_need_python
  local name="$1"; [ -n "$name" ] || die "usage: communicate whereis <name>"
  python3 -c '
import sys, json, glob, os
sessions_dir, state_dir, name = sys.argv[1:4]
peers = {}; bridged = {}
for meta in glob.glob(os.path.join(state_dir, "peers", "*", "meta")):
    d = dict(ln.rstrip("\n").split("=",1) for ln in open(meta) if "=" in ln)
    if d.get("socket"): peers[d["socket"]] = d.get("device","?")
for meta in glob.glob(os.path.join(state_dir, "bridges", "*", "meta")):
    d = dict(ln.rstrip("\n").split("=",1) for ln in open(meta) if "=" in ln)
    if d.get("remote_pid"): bridged[str(d["remote_pid"])] = d.get("device","?")
best=None
for f in glob.glob(os.path.join(sessions_dir, "*.json")):
    try: r=json.load(open(f))
    except Exception: continue
    if r.get("name")==name and r.get("messagingSocketPath"):
        if best is None or r.get("startedAt",0)>best.get("startedAt",0): best=r
if not best: raise SystemExit(3)
sock=best["messagingSocketPath"]; pid=str(best.get("pid",""))
if sock in peers: typ,via="codex",peers[sock]
elif pid in bridged: typ,via="claude",bridged[pid]
else: typ,via="claude","local"
sys.stdout.write("%s\t%s\t%s" % (typ,via,sock))
' "$(comm_sessions_dir)" "$COMM_STATE" "$name"
}

# Route a message to an agent by name — regardless of whether it is a local
# Claude session, a bridged remote Claude session, or a Codex peer. One verb,
# any agent: resolve the name, write the socket.
router_route() {
  local name="$1"; shift || true
  [ -n "$name" ] || die "usage: communicate route <name> [--coach] <message>"
  local -a extra=()
  [ "${1:-}" = "--coach" ] && { extra+=(--coach); shift; }
  [ "$#" -gt 0 ] || die "empty message"
  local info; info="$(router_whereis "$name" 2>/dev/null)" || die "no agent named '$name' (see: communicate agents). Bridge/peer it first."
  local typ via sock
  typ="${info%%$'\t'*}"; info="${info#*$'\t'}"; via="${info%%$'\t'*}"; sock="${info#*$'\t'}"
  log "route -> $name  [$typ via $via]"
  peer_send "$sock" --as "router" "${extra[@]+"${extra[@]}"}" -- "$@"
}
