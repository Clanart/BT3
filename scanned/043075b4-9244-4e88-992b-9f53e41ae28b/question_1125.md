# Q1125: `verify_and_save_emergency_stop_sigs` and compatibility/version negotiation

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port answer or influence the compatibility exchange reached by `verify_and_save_emergency_stop_sigs` in `core/src/rpc/aggregator.rs` so entities proceed with mismatched protocol parameters or circuit ids, producing signatures over transactions the other side interprets differently?

## Target
- File/function: `core/src/rpc/aggregator.rs` -> `verify_and_save_emergency_stop_sigs`
- Entrypoint: a gRPC request to the open port -> `verify_and_save_emergency_stop_sigs`
- Attacker controls: the compatibility payload and the timing of the exchange; attacker is an unprivileged network client who can reach the public gRPC port; holds no certificate, role or key
- Exploit idea: make two entities disagree about the protocol they are executing
- Invariant to test: all entities that sign a deposit's graph agree on the identical paramset and circuit ids
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: assert `verify_and_save_emergency_stop_sigs` refuses to proceed on any parameter mismatch
