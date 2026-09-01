# Q0960: `taproot_builder_with_scripts` and an assumption its callers do not enforce

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port reach `taproot_builder_with_scripts` in `crates/clementine-utils/src/address.rs` from a fund-moving path with input violating an invariant `taproot_builder_with_scripts` documents but does not check (a length, a range, a canonical form), so a bridge transaction is built from a value no caller validated?

## Target
- File/function: `crates/clementine-utils/src/address.rs` -> `taproot_builder_with_scripts`
- Entrypoint: an aggregator request or on-chain value -> `taproot_builder_with_scripts`
- Attacker controls: the value flowing into the helper; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: exploit a validation gap between a helper and its callers
- Invariant to test: every caller of `taproot_builder_with_scripts` on a fund-moving path validates the invariant `taproot_builder_with_scripts` assumes
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: call `taproot_builder_with_scripts` with invariant-violating input and assert it fails closed
