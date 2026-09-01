# Q3068: `verify_and_save_emergency_stop_sigs` and message-size/field-count limits

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port send a message to `verify_and_save_emergency_stop_sigs` in `core/src/rpc/aggregator.rs` whose repeated-field counts (watchtowers, operators, Winternitz keys, signatures) exceed what downstream indexing assumes, so an index derived from one list is applied to another and the wrong key or signature is used for a bridge transaction?

## Target
- File/function: `core/src/rpc/aggregator.rs` -> `verify_and_save_emergency_stop_sigs`
- Entrypoint: a gRPC request to the open port -> `verify_and_save_emergency_stop_sigs`
- Attacker controls: the lengths of every repeated field; attacker is an unprivileged network client who can reach the public gRPC port; holds no certificate, role or key
- Exploit idea: cause cross-list index misuse in transaction construction
- Invariant to test: every index derived from one list is validated against the list it indexes
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: send mismatched list lengths and assert `verify_and_save_emergency_stop_sigs` rejects rather than indexes
