# Q2040: `convert_hex_string_to_bytes` and an assumption its callers do not enforce

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port reach `convert_hex_string_to_bytes` in `core/src/config/protocol.rs` from a fund-moving path with input violating an invariant `convert_hex_string_to_bytes` documents but does not check (a length, a range, a canonical form), so a bridge transaction is built from a value no caller validated?

## Target
- File/function: `core/src/config/protocol.rs` -> `convert_hex_string_to_bytes`
- Entrypoint: an aggregator request or on-chain value -> `convert_hex_string_to_bytes`
- Attacker controls: the value flowing into the helper; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: exploit a validation gap between a helper and its callers
- Invariant to test: every caller of `convert_hex_string_to_bytes` on a fund-moving path validates the invariant `convert_hex_string_to_bytes` assumes
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: call `convert_hex_string_to_bytes` with invariant-violating input and assert it fails closed
