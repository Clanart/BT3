# Q2008: `try_parse_file` and an assumption its callers do not enforce

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port reach `try_parse_file` in `core/src/config/mod.rs` from a fund-moving path with input violating an invariant `try_parse_file` documents but does not check (a length, a range, a canonical form), so a bridge transaction is built from a value no caller validated?

## Target
- File/function: `core/src/config/mod.rs` -> `try_parse_file` (This module defines configuration options)
- Entrypoint: an aggregator request or on-chain value -> `try_parse_file`
- Attacker controls: the value flowing into the helper; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: exploit a validation gap between a helper and its callers
- Invariant to test: every caller of `try_parse_file` on a fund-moving path validates the invariant `try_parse_file` assumes
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: call `try_parse_file` with invariant-violating input and assert it fails closed
