"""The daemon's install/pair payload, shared by every distribution consumer.

The bus UI is packaged separately by Communicate. Historical phone, board and
cockpit applications are not prerequisites for durable identities or seats.
"""

KERNEL_FILES = tuple(sorted((
    "cc_peer.py",
    "homi.py",
    "homi_adopt.py",
    "homi_payload.py",
    "homi_seat.py",
    "homi_workspace.py",
    "model_connections.py",
)))


if __name__ == "__main__":
    import json
    print(json.dumps(KERNEL_FILES))
