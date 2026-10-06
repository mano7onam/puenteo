"""Delivery adapter: messages to ``notes:*`` addresses are appended to the agent's inbox file."""

import json
import os

PUENTEO_API = 1


def push(address: str, msg):
    if not address.startswith("notes:"):
        return None  # not ours: let other adapters / the inbox handle it
    inbox = os.path.expanduser(os.environ.get("NOTES_AGENT_HOME", "~/.notes-agent")) + "/inbox.jsonl"
    with open(inbox, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"from": msg.sender, "id": msg.id, "text": msg.body}) + "\n")
    return "appended to notes inbox"
