# HOMI documentation

Start here:

- [Quickstart](QUICKSTART.md) — install, two identities, a correlated reply, a
  session that knows HOMI, a seat, a bus.
- [Installation](INSTALL.md) — requirements, setup flags, the service, update,
  rollback, uninstall, private configuration, troubleshooting.
- [CLI and delivery semantics](CLI.md) — what each command means and what a
  receipt proves.

Communication:

- [Buses](BUSES.md) — local and connected buses, invitations, self-hosting,
  browser access, the agent graph, accounts and devices, security.
- [Mechanism](MECHANISM.md) — how native session routing works underneath.

Optional modules:

- [Workstation profiles](PROFILES.md) — terminal, mesh, accounts, box, and
  snapshot modules with owned, reversible configuration.
- [Snapshots](SNAPSHOTS.md) — scheduled workspace snapshots and the archive.
- [Contained execution](CONTAINED-EXECUTION.md) — the optional container adapter.

Releasing and qualification:

- [Releasing](RELEASING.md) — building the archive, the qualification matrix,
  publication requirements.
- [Installed-artifact qualification](INSTALLED-QUALIFICATION.md) — the
  checkout-free runner for an extracted release.
- [Client restoration qualification](CLIENT-RESTORATION-QUALIFICATION.md) — real
  Claude Code and Codex registry commands in isolated homes.
- [Provider qualification](PROVIDER-QUALIFICATION.md) — real model exchanges
  from the artifact.
- [Seat provider qualification](SEAT-PROVIDER-QUALIFICATION.md) — seats driven
  by real clients.

Reference:

- [Package README](../packages/communicate/README.md) — the installable unit and
  its development commands.
- [Repository briefing](../AGENTS.md) — boundaries and discipline for contributors.
