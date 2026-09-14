Confirmed. The daemon redacts sensitive fields (`pass`, `key`, `secret`, `mnemonic`) only for the `"Received message: %s"` debug log at [1](#0-0) , but the `handle_message()` unknown-command branch logs the raw, unredacted `message` dict at error level via `self.log.error(f"UK>> {message}")` [2](#0-1) . This is reachable by any authenticated local daemon/RPC client sending a command not present in `keychain_commands` or `get_command_mapping()`, with a `data` payload containing `mnemonic`, `private_key`, `passphrase`, etc. — bypassing the same redaction mechanism the codebase itself considers a mandatory control (per `.cursor/context/daemon.md`: "Debug logging must pass through `redact_sensitive_data()` before recording message contents") and per the existing regression test `test_message_logging_redaction` at [3](#0-2) .

### Title
Unknown-command handler logs unredacted daemon RPC payloads, leaking keychain secrets - (File: chia/daemon/server.py)

### Summary
`WebSocketServer.handle_message()` redacts sensitive keys (mnemonic, private_key, passphrase, secret, password, etc.) only when logging the initial "Received message" debug line. When the incoming `command` does not match any keychain command or entry in `get_command_mapping()`, the raw, unredacted message dict is logged at `ERROR` level via `self.log.error(f"UK>> {message}")`.

### Finding Description
The daemon's `redact_sensitive_data()` helper is applied once, right after decoding the incoming JSON, to build a `redacted_message` used solely for the `"Received message: %s"` debug log at [4](#0-3) . The original, non-redacted `decoded` dict is what's actually passed into `handle_message(ws, decoded)` for dispatch [5](#0-4) .

Inside `handle_message()`, if the `command` isn't `"ping"`, isn't in `keychain_commands`, and isn't a key of `self.get_command_mapping()`, the code falls into an `else` branch that logs the entire original `message` object — unredacted — at `ERROR` level, then returns an `unknown_command` error response: [2](#0-1) .

Because any RPC client that can reach the daemon websocket (wallet UI, local RPC callers, or any process holding valid daemon TLS client cert / local access) fully controls both the `command` string and the `data` dict, they can trivially trigger this branch by sending a bogus/unsupported `command` (or a slightly misspelled valid command) alongside a payload embedding sensitive fields — for example a `mnemonic`, `private_key`, or `passphrase` field they've deliberately inserted (or that gets echoed/forwarded from another system into a mistyped command). The `redact_sensitive_data()` control that the codebase's own architecture notes call mandatory ("Debug logging must pass through `redact_sensitive_data()` before recording message contents", `.cursor/context/daemon.md` line 63-64) is bypassed for this specific log call.

### Impact Explanation
Daemon logs (which are frequently collected, shipped to support bundles, or accessible to less-privileged processes/users than the keyring itself) can end up containing plaintext mnemonics, private keys, or passphrases if a client (malicious, buggy, or a proxy component mistyping a command name) sends such data under an unrecognized command. This matches the CWE-532/CWE-200/CWE-212 bug class of the referenced advisory (sensitive OAuth/secret data leaking through debug logging paths that were supposed to be redacted). Log exposure of a wallet mnemonic or private key is equivalent to full compromise of that key's funds.

### Likelihood Explanation
Exploitability requires the ability to send arbitrary JSON to the daemon websocket, which per `daemon.md` is gated by mutual TLS with daemon-issued client certs — i.e., it is reachable by any local component holding a legitimate daemon client cert (GUI, CLI, third-party integrations, or a compromised/buggy local service), not just the maintainers. Given the daemon already went through the trouble of adding `redact_sensitive_data()` specifically to prevent exactly this leak class (and added a dedicated regression test for the happy path), the unknown-command branch appears to be an overlooked bypass of that same intended protection, making accidental triggering (e.g., a client typo in a command name while including a mnemonic field) plausible during normal operation, not just deliberate attack.

### Recommendation
Route the unknown-command log line through the same `redact_sensitive_data()` helper used at the "Received message" log site, e.g. `self.log.error("UK>> %s", redact_sensitive_data(message))`, and add a regression test analogous to `test_message_logging_redaction` that exercises the unknown-command path.

### Proof of Concept
1. Connect to the local daemon websocket with a valid client cert (as any local wallet/RPC client would).
2. Send a JSON RPC message with `"destination": "daemon"`, `"command": "not_a_real_command"`, and `"data": {"mnemonic": "<24 secret words>", "private_key": "<hex sk>"}`.
3. Observe the daemon's log output at ERROR level containing `UK>> {...}` with the full, unredacted `mnemonic` and `private_key` values, unlike the "Received message" debug line for the same payload which would have shown `***<redacted>***`.

### Citations

**File:** chia/daemon/server.py (L290-298)
```python
            if msg.type == WSMsgType.TEXT:
                try:
                    decoded = json.loads(msg.data)
                    if "data" not in decoded:
                        decoded["data"] = {}

                    redacted_data = redact_sensitive_data(decoded)
                    redacted_message = WSMessage(msg.type, redacted_data, msg.extra)
                    self.log.debug("Received message: %s", redacted_message)
```

**File:** chia/daemon/server.py (L300-300)
```python
                    maybe_response = await self.handle_message(ws, decoded)
```

**File:** chia/daemon/server.py (L440-446)
```python
        else:
            command_mapping = self.get_command_mapping()
            if command in command_mapping:
                response = await command_mapping[command](websocket=websocket, request=data)
            else:
                self.log.error(f"UK>> {message}")
                response = {"success": False, "error": f"unknown_command {command}"}
```

**File:** chia/_tests/core/daemon/test_daemon.py (L2139-2199)
```python
@pytest.mark.anyio
async def test_message_logging_redaction(
    daemon_connection_and_temp_keychain: tuple[aiohttp.ClientWebSocketResponse, Keychain],
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws, _ = daemon_connection_and_temp_keychain

    with caplog.at_level(logging.DEBUG, logger="chia.daemon.server"):
        sensitive_payload = create_payload(
            "test_command",
            {
                "password": "secret_password",
                "private_key": "sensitive_key_data",
                "secret_value": "very_secret",
                "mnemonic": "test_mnemonic_phrase",
                "normal_field": "normal_value",
                "nested_object": {
                    "passphrase": "nested_secret",
                    "api_key": "nested_api_key",
                    "seed_mnemonic": "nested_mnemonic",
                    "safe_field": "safe_value",
                },
            },
            "test",
            "daemon",
        )

        original_message = json.loads(sensitive_payload)
        request_id = original_message["request_id"]

        await ws.send_str(sensitive_payload)
        await ws.receive()

        log_message = next(record for record in caplog.records if "Received message:" in record.message).message
        _, _, ws_message_str = log_message.partition("Received message: ")

        # Build the expected redacted structure and sort keys like dict_to_json_str does
        expected_redacted_data = {
            "ack": False,
            "command": "test_command",
            "data": {
                "mnemonic": "***<redacted>***",
                "nested_object": {
                    "api_key": "***<redacted>***",
                    "passphrase": "***<redacted>***",
                    "safe_field": "safe_value",
                    "seed_mnemonic": "***<redacted>***",
                },
                "normal_field": "normal_value",
                "password": "***<redacted>***",
                "private_key": "***<redacted>***",
                "secret_value": "***<redacted>***",
            },
            "destination": "daemon",
            "origin": "test",
            "request_id": request_id,
        }

        expected_ws_message = f"WSMessage(type=<WSMsgType.TEXT: 1>, data={expected_redacted_data!r}, extra='')"

        assert ws_message_str == expected_ws_message
```
