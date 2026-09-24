# Release qualification

Test the actual artifact, installation and advertised behavior together. Existing
native communication, buses, durable HOMI and selected terminal helpers are the
baseline implementation.

## Build

Commit source and keep root/package/plugin release versions consistent:

```sh
python3 scripts/build-release.py
python3 scripts/render-homebrew-formula.py VERSION SHA256 /path/to/tap/Formula/homi.rb
```

The builder uses the committed lockfile and includes production dependencies and
notices. Archive metadata is normalized; the manifest records source commit,
dependency integrity and file hashes. `--allow-dirty` is development-only. Publish
only the immutable archive whose checksum was actually tested. Formula installation
must not configure the user's machine automatically.

## Qualification

1. Run affected native/bus/durable/seat suites and integration tests.
2. Validate manifests, skills and CLI/MCP from the packed runtime.
3. Exercise fresh/repeat setup, upgrade, failed activation, rollback, selective
   uninstall and preserved-state reinstall in isolated environments.
4. Qualify optional terminal/mesh independently from core install.
5. On designated devices, disable conflicting legacy integration reversibly and
   install the artifact as a user would, without source-checkout fallback.
6. Open fresh supported clients and prove creation, exact registration, discovery,
   messaging, replies and dashboard/inbox operation with bounded test identities.
7. Prove cross-device enrollment/communication using the installed payloads.
8. Record platform/runtime versions and pass/fail/skipped results. Missing
   prerequisites do not establish support. Terminal delivery does not prove
   desktop wake; queue acceptance does not prove model consumption.

Use isolated fixtures on the developer's working Mac. Do not disable its existing
integrations for qualification; actual legacy-disabled installation testing belongs
on designated test devices.

CI runs the retained core gate on Linux and macOS with Node 20 and 22, then extracts
the built archive and runs `scripts/qualify-installed.py` against it. Its JSON
report is retained independently from the archive. Any explicitly unqualified
upgrade check remains open; a successful process exit does not turn omitted live
provider or previous-release checks into passes. See
[Provider and client qualification](PROVIDER-QUALIFICATION.md) for the opt-in
artifact harness, fresh installed-plugin acceptance, and two-device proof.

The dashboard job uses Playwright 1.63.0 with Chromium against the built archive's
actual assets. Run the same gate locally with
`uv run --with playwright==1.63.0 python scripts/qualify-browser.py /path/to/runtime --evidence /private/new-ui-evidence`
after installing its Chromium binary. It covers graph/conductor and human-inbox
behavior using isolated API fixtures; it does not establish real model delivery.

## Publication

Audit all retained Git refs/history, current source and archive. Verify historical
credential status and confidential material. Preserve a private backup before any
history treatment. Do not publish while a gate remains unresolved.

Include MIT and third-party notices. Publish the tested commit/tag, archive,
checksums, release notes and support matrix, then update the tap. Verify anonymous
download/install. npm publication is optional; never advertise an unpublished scope.
Public distribution does not grant access to private hosted buses.
