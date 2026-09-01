# Q0568: `default_tx_sender_limits` and an assumption its callers do not enforce

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port reach `default_tx_sender_limits` in `core/src/config/mod.rs` from a fund-moving path with input violating an invariant `default_tx_sender_limits` documents but does not check (a length, a range, a canonical form), so a bridge transaction is built from a value no caller validated?

## Target
- File/function: `core/src/config/mod.rs` -> `default_tx_sender_limits` (This module defines configuration options)
- Entrypoint: an aggregator request or on-chain value -> `default_tx_sender_limits`
- Attacker controls: the value flowing into the helper; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: exploit a validation gap between a helper and its callers
- Invariant to test: every caller of `default_tx_sender_limits` on a fund-moving path validates the invariant `default_tx_sender_limits` assumes
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: call `default_tx_sender_limits` with invariant-violating input and assert it fails closed
