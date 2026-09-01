# Q2168: `default_utxo_amount` and an assumption its callers do not enforce

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port reach `default_utxo_amount` in `crates/clementine-config/src/protocol.rs` from a fund-moving path with input violating an invariant `default_utxo_amount` documents but does not check (a length, a range, a canonical form), so a bridge transaction is built from a value no caller validated?

## Target
- File/function: `crates/clementine-config/src/protocol.rs` -> `default_utxo_amount` (Protocol parameters for the clementine bridge)
- Entrypoint: an aggregator request or on-chain value -> `default_utxo_amount`
- Attacker controls: the value flowing into the helper; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: exploit a validation gap between a helper and its callers
- Invariant to test: every caller of `default_utxo_amount` on a fund-moving path validates the invariant `default_utxo_amount` assumes
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: call `default_utxo_amount` with invariant-violating input and assert it fails closed
