# Q3686: `send_move_to_vault_tx` and compatibility/version negotiation

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port answer or influence the compatibility exchange reached by `send_move_to_vault_tx` in `core/src/rpc/aggregator.rs` so entities proceed with mismatched protocol parameters or circuit ids, producing signatures over transactions the other side interprets differently?

## Target
- File/function: `core/src/rpc/aggregator.rs` -> `send_move_to_vault_tx`
- Entrypoint: a gRPC request to the open port -> `send_move_to_vault_tx`
- Attacker controls: the compatibility payload and the timing of the exchange; attacker is an unprivileged network client who can reach the public gRPC port; holds no certificate, role or key
- Exploit idea: make two entities disagree about the protocol they are executing
- Invariant to test: all entities that sign a deposit's graph agree on the identical paramset and circuit ids
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: assert `send_move_to_vault_tx` refuses to proceed on any parameter mismatch
