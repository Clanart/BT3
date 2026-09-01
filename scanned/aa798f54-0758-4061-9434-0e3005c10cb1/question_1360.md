# Q1360: `iter_rounds_range` and an assumption its callers do not enforce

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port reach `iter_rounds_range` in `crates/clementine-primitives/src/lib.rs` from a fund-moving path with input violating an invariant `iter_rounds_range` documents but does not check (a length, a range, a canonical form), so a bridge transaction is built from a value no caller validated?

## Target
- File/function: `crates/clementine-primitives/src/lib.rs` -> `iter_rounds_range` (Primitive types shared across clementine crates)
- Entrypoint: an aggregator request or on-chain value -> `iter_rounds_range`
- Attacker controls: the value flowing into the helper; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: exploit a validation gap between a helper and its callers
- Invariant to test: every caller of `iter_rounds_range` on a fund-moving path validates the invariant `iter_rounds_range` assumes
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: call `iter_rounds_range` with invariant-violating input and assert it fails closed
