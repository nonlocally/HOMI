"""homi adopt: one command onboards a device where you have an account.

Everything the fleet needed by hand — encoded. The ceremony:
  probe -> plan -> provision -> pair -> (spawn) -> human checklist

The PLANNER is pure (facts in, actions out) so every field lesson is a unit
test, not tribal knowledge:
  - reverse keys: the far device dials the hub with its OWN key, installed
    during adopt over the already-authenticated channel (never a laptop's
    forwarded agent — mini-1 died of that).
  - dial address: when the hub's bare device name doesn't resolve from the
    far side (off-tailnet peer-device, MagicDNS-less mw83), the address
    that provably works is the one this very ssh connection came from —
    $SSH_CONNECTION — written as a Host alias.
  - runtime dir: claude puts its cross-session socket in
    $XDG_RUNTIME_DIR/cc-socks falling back to /tmp/cc-socks; on a shared
    mac another uid owns /tmp/cc-socks and live delivery silently dies.
    Darwin has no systemd runtime dirs, so provision ~/.local/run always;
    Linux only when $XDG_RUNTIME_DIR is unset (Termux).
  - kernel: hash-compared, never version-string-compared (pair kept stale
    bytes on peer-device AND mw83); refresh restarts the far daemon.
  - CLI shim + spawn (settings via a FILE — inline JSON dies in nested
    shell quoting, proven twice).
Steps that genuinely need the human (claude /login, a Tailscale SSH check)
are detected and printed as a checklist, not discovered by timeout.
"""
import os
import shlex
import subprocess
import time

# The one kernel list — homi.py's stage list is asserted (by test) to match.
KERNEL_FILES = sorted([
    "homi.py", "cc_peer.py", "homi_seat.py", "homi_workspace.py",
    "homi_board.py", "homi_talk.py", "homi_transcript.py", "homi_codex.py",
    "homi_adopt.py",
])

_FAR_PATH = "/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"


# ---------------------------------------------------------------- probe

def probe_script(my_addr):
    """One remote sh script emitting KEY=VALUE lines — a single round-trip.
    my_addr: how the far side would dial the hub (user@devname)."""
    kf = " ".join("~/.local/share/homi/daemon/current/%s" % f
                  for f in KERNEL_FILES)
    return (
        'echo "OS=$(uname -s)"; echo "HOMEDIR=$HOME"; echo "SHELL_=$SHELL"; '
        'echo "XDG=$XDG_RUNTIME_DIR"; '
        'echo "SSHIP=${SSH_CONNECTION%%%% *}"; '
        'o=$(stat -f %%u /tmp/cc-socks 2>/dev/null || '
        'stat -c %%u /tmp/cc-socks 2>/dev/null); '
        'echo "CCOWNER=$o"; echo "UID_=$(id -u)"; '
        '[ -f ~/.ssh/id_ed25519.pub ] && echo "OWNKEY=1" || echo "OWNKEY=0"; '
        'command -v python3 >/dev/null && echo "PY3=1" || echo "PY3=0"; '
        'echo "TMUX_BIN=$(PATH=%(p)s command -v tmux)"; '
        'echo "CLAUDE_BIN=$(PATH=%(p)s command -v claude)"; '
        'echo "SHIM=$([ -x ~/.local/bin/communicate ] && echo 1 || echo 0)"; '
        'echo "KHASH=$(cat %(kf)s 2>/dev/null | md5 -q 2>/dev/null || '
        'cat %(kf)s 2>/dev/null | md5sum 2>/dev/null | cut -d" " -f1)"; '
        'ssh -o BatchMode=yes -o ConnectTimeout=6 %(me)s true 2>/dev/null '
        '&& echo "REV=1" || echo "REV=0"'
        % {"p": _FAR_PATH, "kf": kf, "me": shlex.quote(my_addr)}
    )


def parse_facts(out):
    """KEY=VALUE lines -> facts dict. Banners and junk are ignored; a
    missing key gets a safe default (absent/false)."""
    kv = {}
    for line in (out or "").splitlines():
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        if k.isupper() and k.replace("_", "").isalnum():
            kv[k] = v.strip()
    owner, uid = kv.get("CCOWNER", ""), kv.get("UID_", "")
    return {
        "os": kv.get("OS", ""),
        "home": kv.get("HOMEDIR", ""),
        "login_shell": kv.get("SHELL_", ""),
        "xdg": kv.get("XDG", ""),
        "ssh_ip": kv.get("SSHIP", ""),
        "cc_collision": bool(owner) and bool(uid) and owner != uid,
        "own_key": kv.get("OWNKEY") == "1",
        "py3": kv.get("PY3") == "1",
        "tmux_bin": kv.get("TMUX_BIN", ""),
        "claude_bin": kv.get("CLAUDE_BIN", ""),
        "shim": kv.get("SHIM") == "1",
        "kernel_hash": kv.get("KHASH", ""),
        "reverse_ok": kv.get("REV") == "1",
    }


# ----------------------------------------------------------------- plan

def _profile_files(login_shell):
    sh = os.path.basename(login_shell or "")
    if sh == "zsh":
        return ["~/.zshenv"]
    if sh == "bash":
        return ["~/.bash_profile", "~/.bashrc"]
    return ["~/.profile"]


def plan(facts, local):
    """facts (probed) + local {kernel_hash, my_addr} -> (actions, checklist).
    Pure. Ordering: keys -> alias -> runtime dir -> shim -> kernel."""
    acts, checklist = [], []

    if not facts.get("own_key"):
        acts.append({"step": "gen_own_key"})
    if not facts.get("reverse_ok"):
        # The far side must dial the hub with its own key: authorize it
        # here (dedup-checked on apply), and give it a resolvable address.
        acts.append({"step": "authorize_key_here"})
        if facts.get("ssh_ip"):
            acts.append({"step": "reverse_alias", "ip": facts["ssh_ip"]})
        else:
            checklist.append(
                "reverse path: could not learn the hub's address from "
                "SSH_CONNECTION — on the device, make `ssh %s` work "
                "(alias or DNS), then re-run adopt" % local.get("my_addr"))

    if facts.get("os") == "Darwin" and not facts.get("xdg"):
        # No systemd runtime dirs on macOS: provision before the first
        # collision, not after live delivery silently dies.
        acts.append({"step": "runtime_dir",
                     "profiles": _profile_files(facts.get("login_shell"))})
    elif facts.get("os") != "Darwin" and not facts.get("xdg"):
        acts.append({"step": "runtime_dir",
                     "profiles": _profile_files(facts.get("login_shell"))})

    if not facts.get("shim"):
        acts.append({"step": "shim"})

    if not facts.get("tmux_bin"):
        if facts.get("os") == "Darwin":
            checklist.append("tmux missing and no static build for macOS — "
                             "install it (brew install tmux), then re-run")
        else:
            acts.append({"step": "install_tmux_static"})
    if not facts.get("claude_bin"):
        acts.append({"step": "install_claude"})

    far, mine = facts.get("kernel_hash"), local.get("kernel_hash")
    if far and mine and far != mine:
        # A staged-but-stale kernel: refresh bytes AND restart the daemon
        # that loaded the old ones. An ABSENT kernel is pair's own step.
        acts.append({"step": "kernel_refresh"})
        acts.append({"step": "restart_daemon"})

    return acts, checklist


# ------------------------------------------------------------ executors

def settings_file_cmd():
    """Shell line writing ~/.homi-settings.json — printf-encoded so the
    JSON never meets nested shell quoting (the peer-device lesson)."""
    return (r'printf "{\"crossSessionInbound\":\"accept\"}\n" '
            r'> "$HOME/.homi-settings.json"')


def spawn_cmds(name, facts):
    """Remote commands that spawn agent NAME in tmux session homi-NAME.
    Interactive shell + send-keys (claude needs a real pty; a launcher
    script as the pane command died twice). Returned as a list of remote
    sh lines to run in order, with waits handled by the caller."""
    tmux = facts.get("tmux_bin") or "tmux"
    env = ("export XDG_RUNTIME_DIR=\\\"\\$HOME/.local/run\\\"; "
           if facts.get("os") == "Darwin" else "")
    return [
        settings_file_cmd(),
        "%s kill-session -t homi-%s 2>/dev/null; "
        "%s new-session -d -s homi-%s -x 200 -y 50" % (tmux, name, tmux, name),
        "%s send-keys -t homi-%s \"%sexport PATH=%s; "
        "cd ~ && claude --settings ~/.homi-settings.json\" Enter"
        % (tmux, name, env, _FAR_PATH.replace("$", "\\$")),
        "%s send-keys -t homi-%s Enter" % (tmux, name),          # trust prompt
        "%s send-keys -t homi-%s \"/rename %s\" Enter" % (tmux, name, name),
    ]


def _run_ssh(addr, cmd, timeout=60):
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                        addr, cmd], capture_output=True, text=True,
                       timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _local_kernel_hash(here_dir):
    import hashlib
    h = hashlib.md5()
    for f in KERNEL_FILES:
        try:
            with open(os.path.join(here_dir, f), "rb") as fh:
                h.update(fh.read())
        except OSError:
            pass
    return h.hexdigest()


def _authorize_here(pubkey, tag):
    """Append a far device's pubkey to ~/.ssh/authorized_keys, deduped on
    the key material itself."""
    pubkey = (pubkey or "").strip()
    if not pubkey.startswith("ssh-"):
        return False
    material = pubkey.split()[1] if len(pubkey.split()) > 1 else ""
    ak = os.path.expanduser("~/.ssh/authorized_keys")
    try:
        existing = open(ak).read()
    except OSError:
        existing = ""
    if material and material in existing:
        return True
    with open(ak, "a") as f:
        f.write("%s %s %s\n" % (pubkey.split()[0], material, tag))
    os.chmod(ak, 0o600)
    return True


def execute(addr, acts, facts, local, ssh=_run_ssh, say=print):
    """Run the planned actions. Each prints one honest line."""
    devtag = "adopt-%s" % addr.split("@")[-1].split(".")[0]
    for a in acts:
        step = a["step"]
        if step == "gen_own_key":
            ssh(addr, "ssh-keygen -t ed25519 -N '' -q -f ~/.ssh/id_ed25519 "
                      "</dev/null 2>/dev/null; true")
            say("  provision: own key       generated")
        elif step == "authorize_key_here":
            rc, out = ssh(addr, "cat ~/.ssh/id_ed25519.pub")
            done = rc == 0 and _authorize_here(out, devtag)
            say("  provision: reverse key   %s" %
                ("authorized on this hub" if done else "FAILED to fetch"))
        elif step == "reverse_alias":
            host = local["my_addr"].split("@")[-1]
            user = local["my_addr"].split("@")[0]
            ssh(addr, "mkdir -p ~/.ssh; chmod 700 ~/.ssh; "
                      "grep -q 'Host %s' ~/.ssh/config 2>/dev/null || "
                      "printf '\\nHost %s\\n  HostName %s\\n  User %s\\n' "
                      ">> ~/.ssh/config; chmod 600 ~/.ssh/config"
                      % (host, host, a["ip"], user))
            rc, _ = ssh(addr, "ssh -o BatchMode=yes -o ConnectTimeout=8 "
                              "-o StrictHostKeyChecking=accept-new %s true"
                        % shlex.quote(local["my_addr"]))
            say("  provision: dial alias    %s -> %s (%s)"
                % (host, a["ip"], "verified" if rc == 0 else "NOT VERIFIED"))
        elif step == "runtime_dir":
            lines = ("export XDG_RUNTIME_DIR=\\\"\\$HOME/.local/run\\\"\\n"
                     "[ -d \\\"\\$XDG_RUNTIME_DIR\\\" ] || "
                     "mkdir -p \\\"\\$XDG_RUNTIME_DIR\\\"\\n"
                     "chmod 700 \\\"\\$XDG_RUNTIME_DIR\\\" 2>/dev/null\\n")
            for prof in a["profiles"]:
                ssh(addr, "grep -q XDG_RUNTIME_DIR %s 2>/dev/null || "
                          "printf \"%s\" >> %s" % (prof, lines, prof))
            ssh(addr, "mkdir -p ~/.local/run && chmod 700 ~/.local/run")
            say("  provision: runtime dir   ~/.local/run (%s)"
                % ", ".join(a["profiles"]))
        elif step == "shim":
            ssh(addr, "mkdir -p ~/.local/bin; "
                      "printf '#!/bin/sh\\n[ \"$1\" = homi ] && shift\\n"
                      "exec python3 \"$HOME/.local/share/homi/daemon/current/"
                      "homi.py\" call \"$@\"\\n' > ~/.local/bin/communicate; "
                      "chmod +x ~/.local/bin/communicate; "
                      "ln -sf ~/.local/bin/communicate ~/.local/bin/homi")
            say("  provision: CLI shim      ~/.local/bin/{communicate,homi}")
        elif step == "install_tmux_static":
            rc, _ = ssh(addr, "mkdir -p ~/.local/bin && curl -fsSL -o /tmp/tmux.gz "
                        "https://github.com/mjakob-gh/build-static-tmux/releases/"
                        "latest/download/tmux.linux-amd64.gz && gunzip -f /tmp/tmux.gz "
                        "&& mv /tmp/tmux ~/.local/bin/tmux && chmod +x ~/.local/bin/tmux "
                        "&& ~/.local/bin/tmux -V", timeout=180)
            say("  provision: tmux          %s"
                % ("static build installed" if rc == 0 else "INSTALL FAILED"))
        elif step == "install_claude":
            rc, _ = ssh(addr, "curl -fsSL https://claude.ai/install.sh | bash "
                              ">/dev/null 2>&1; PATH=%s command -v claude"
                        % _FAR_PATH, timeout=300)
            say("  provision: claude        %s"
                % ("installed" if rc == 0 else "INSTALL FAILED"))
        elif step == "kernel_refresh":
            here_dir = local["here_dir"]
            files = [os.path.join(here_dir, f) for f in KERNEL_FILES
                     if os.path.exists(os.path.join(here_dir, f))]
            r = subprocess.run(["scp", "-q", "-o", "BatchMode=yes"] + files +
                               ["%s:.local/share/homi/daemon/current/" % addr],
                               capture_output=True, text=True, timeout=120)
            say("  provision: kernel        %s"
                % ("refreshed (hash mismatch)" if r.returncode == 0
                   else "REFRESH FAILED"))
        elif step == "restart_daemon":
            ssh(addr, "pkill -f 'homi.py daemon' 2>/dev/null; sleep 1; "
                      "nohup python3 ~/.local/share/homi/daemon/current/homi.py "
                      "daemon >> ~/.local/state/communicate/homi/daemon.log "
                      "2>&1 & sleep 2; pgrep -f 'homi.py daemon' | head -1")
            say("  provision: daemon        restarted on the new kernel")


def spawn(addr, name, facts, fardev, ssh=_run_ssh, say=print, ask=None):
    """Spawn agent NAME on the device and prove it end to end."""
    cmds = spawn_cmds(name, facts)
    ssh(addr, cmds[0])
    ssh(addr, cmds[1])
    ssh(addr, cmds[2])
    time.sleep(15)
    ssh(addr, cmds[3])            # trust prompt: accept
    time.sleep(12)
    ssh(addr, cmds[4])            # /rename
    time.sleep(8)
    rc, out = ssh(addr,
                  "python3 -c \"import glob,json,os\n"
                  "for f in glob.glob(os.path.expanduser('~/.claude/sessions/*.json')):\n"
                  "    try: d=json.load(open(f))\n"
                  "    except Exception: continue\n"
                  "    if d.get('name')=='%s' and d.get('version')!='communicate-homi':\n"
                  "        print('REG', d.get('pid')); break\"" % name)
    registered = "REG" in out
    say("  spawn: %-18s %s" % (name, "registered" if registered
                               else "NOT REGISTERED — check the pane"))
    if registered and ask:
        okd, reply = ask("%s@%s" % (name, fardev),
                         "reply exactly: %s-adopted" % name)
        say("  spawn: round trip        %s"
            % (("verified (%s)" % reply) if okd else "no reply yet — "
               "if claude needs /login it will answer after"))
        return okd
    return registered
