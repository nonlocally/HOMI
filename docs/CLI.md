# CLI and delivery semantics

`homi` is the product entry point; existing `communicate` operations retain their
meaning. Resolve names within the selected address space. Failure must not create
a substitute agent or target another route silently.

| Operation | Meaning |
|---|---|
| `homi claim NAME` | Persistent identity with saved messages; no model launch implied. |
| `homi spawn NAME --cli claude --cwd DIR` | Start and bind an execution. |
| `homi spawn NAME --cli codex --model-connection CONNECTION` | Start the client using an explicitly selected model connection (v0.5). |
| `homi model list --json` | Inspect configured model connections without exposing keys. |
| `homi model doctor CONNECTION --json` | Check the selected API catalog; no inference test. |
| `homi model run CONNECTION --cli claude` | Launch the client with that model, preserving ordinary defaults. |
| `homi send NAME MESSAGE` | Durable mail. |
| `homi inbox NAME` | Stored JSONL inbox; reading does not imply acknowledgement. |
| `homi ask NAME QUESTION --timeout SEC` | Existing durable request/reply path. |
| `homi seat ls` | Execution endpoints for the configured seat driver. |
| `homi bus register` | Exact current session on the configured broker. |
| `homi bus agents --json` | Registered sessions on that broker. |
| `homi bus send ID --bus NAME -- MESSAGE` | Bus delivery with membership checks. |
| `homi native agents` | Existing native socket/SSH routing table. |
| `homi native route NAME MESSAGE` | Native session route. |
| `homi profile preview --terminal --mesh` | Preview optional configuration. |

Run `homi start` for an unmanaged daemon or `homi setup --service` for persistence.
A supervisor may restart a managed daemon after `homi stop`. `homi uninstall`
removes the owned service, client integrations and executable links while preserving
saved identities, mail, histories and credentials. `--no-clients` preserves client
registrations during removal, but still removes owned executable links; it is not
a daemon-only operation. Legacy `homi daemon install|uninstall` and
`communicate homi install|uninstall` refuse service changes because they lack the
managed lifecycle's ownership record. Use `homi setup --service` for persistence.

`homi daemon pair USER@HOST` enrolls another of your devices. It discovers the far
daemon in this order: an installed HOMI release (`~/.local/share/communicate/current`,
run through its own `homi` CLI and upgraded only by `homi update` there), a kernel
pair itself staged earlier, then `communicate` on the remote PATH. It never stages a
kernel over an installed release, never overwrites a service definition it did not
write (the fix is `homi setup --service` on that device, or `--no-persist`), and
backs its own previous definition up beside it before refreshing it.

Model connections configure execution, not identity or bus membership. See
[model connections](MODELS.md) for private setup, client requirements and
qualification limits. These commands are on the v0.5 development branch.

## Receipts

- **Stored:** the identity's message store or broker accepted the message durably.
- **Submitted:** a provider socket, queue or terminal accepted input; retain any
  uncertainty about consumption.
- **Replied:** a response correlated to the request arrived.

Not every transport implements every level. Legacy weak reply fallbacks are not
strong correlation. Codex queue success does not establish wake or consumption by
the exact session. Screen capture is execution observation, not an agent reply.

Native filesystem/SSH trust, broker membership, and remote seat control are separate
capabilities. A bus invitation does not authorize arbitrary shell control. A pane
is not a container; select isolation explicitly when needed.
