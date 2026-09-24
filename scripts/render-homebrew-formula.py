#!/usr/bin/env python3
"""Generate a formula for an immutable, checksum-verified HOMI release archive."""
import argparse
import re
from pathlib import Path

TEMPLATE = '''class Homi < Formula
  desc "Persistent agent identities, messaging, and execution"
  homepage "https://github.com/nonlocally/HOMI"
  url "https://github.com/nonlocally/HOMI/releases/download/v@VERSION@/homi-@VERSION@.tar.gz"
  sha256 "@SHA256@"
  license "MIT"

  depends_on "bash"
  depends_on "node"
  depends_on "python@3.14"

  def install
    libexec.install Dir["*"]
    (bin/"homi").write_env_script libexec/"bin/homi",
      PATH: "#{Formula["node"].opt_bin}:#{Formula["python@3.14"].opt_bin}:#{Formula["bash"].opt_bin}:$PATH"
    (bin/"communicate").write_env_script libexec/"bin/communicate",
      PATH: "#{Formula["node"].opt_bin}:#{Formula["python@3.14"].opt_bin}:#{Formula["bash"].opt_bin}:$PATH"
  end

  def caveats
    <<~EOS
      Enable your agent integrations explicitly:
        homi setup --claude --codex
        homi doctor

      To enable the persistent local daemon:
        homi setup --service

      Terminal and mesh profiles are optional. Preview before applying:
        homi profile preview --terminal --mesh

      Install tmux for agent panes; fzf and jq for the optional terminal/mesh profile.
      Model clients and their authentication are managed separately.
      Installing or upgrading this formula does not replace your terminal configuration.
    EOS
  end

  test do
    assert_match "@VERSION@", shell_output("#{bin}/homi version")
    assert_match "bus", shell_output("#{bin}/homi --help")
    ENV["HOME"] = testpath
    ENV["COMMUNICATE_DATA"] = testpath/"data"
    ENV["COMM_STATE"] = testpath/"state"
    system bin/"homi", "setup", "--no-clients", "--dry-run"
    refute_path_exists testpath/"data"
  end
end
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("version")
    p.add_argument("sha256")
    p.add_argument("output", type=Path)
    args = p.parse_args()
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?", args.version):
        p.error("invalid release version")
    if not re.fullmatch(r"[0-9a-f]{64}", args.sha256):
        p.error("invalid SHA-256")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(TEMPLATE.replace("@VERSION@", args.version).replace("@SHA256@", args.sha256))


if __name__ == "__main__":
    main()
