# Q2464: `anchor_amount` and an assumption its callers do not enforce

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port reach `anchor_amount` in `crates/clementine-config/src/protocol.rs` from a fund-moving path with input violating an invariant `anchor_amount` documents but does not check (a length, a range, a canonical form), so a bridge transaction is built from a value no caller validated?

## Target
- File/function: `crates/clementine-config/src/protocol.rs` -> `anchor_amount` (Protocol parameters for the clementine bridge)
- Entrypoint: an aggregator request or on-chain value -> `anchor_amount`
- Attacker controls: the value flowing into the helper; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: exploit a validation gap between a helper and its callers
- Invariant to test: every caller of `anchor_amount` on a fund-moving path validates the invariant `anchor_amount` assumes
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: call `anchor_amount` with invariant-violating input and assert it fails closed
