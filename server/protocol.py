"""Wire framing for the ChatCC WebSocket.

Each frame is a text message: a single opcode char followed by a compact JSON
payload. Keeping it text (not packed binary) makes it trivial to parse in
ComputerCraft Lua via textutils.unserialiseJSON, and chat volume is low enough
that the overhead is irrelevant.

Opcodes:
    M  chat message   {"id","a"(author),"m"(text),"r"(role),"t"(type)}
    S  status         {"s"(state),"m"(message)}
    D  device prompt  {"url","code","expires"}      (login flow)
    A  login ok       {"token","account"}           (login flow)

Roles: owner | moderator | member | verified | user
States: connecting | live | ended | error
"""

import json


def _frame(op: str, payload: dict) -> str:
    return op + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def message(msg_id: str, author: str, text: str, role: str, mtype: str) -> str:
    return _frame("M", {"id": msg_id, "a": author, "m": text, "r": role, "t": mtype})


def status(state: str, msg: str = "") -> str:
    return _frame("S", {"s": state, "m": msg})


def device_prompt(url: str, code: str, expires: int) -> str:
    return _frame("D", {"url": url, "code": code, "expires": expires})


def login_ok(auth_token: str, account: str) -> str:
    return _frame("A", {"token": auth_token, "account": account})
