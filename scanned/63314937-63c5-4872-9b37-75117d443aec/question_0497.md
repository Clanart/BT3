# Q497: Misbind trusted context inside send_move_to_vault_tx

## Question
Can an unprivileged attacker reach `send_move_to_vault_tx` through public gRPC `ClementineAggregator.SendMoveToVaultTx` request with attacker-supplied raw move transaction bytes and make attacker-controlled the transaction witness, anchor output, and script-path structure bind to the wrong trusted context, so the vault destination and anchor/output structure is interpreted for one bridge action while authorizing another, violating the invariant that the raw move transaction must be structurally valid and bound to the exact deposit it finalizes, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/rpc/aggregator.rs::send_move_to_vault_tx
- Entrypoint: public gRPC `ClementineAggregator.SendMoveToVaultTx` request with attacker-supplied raw move transaction bytes
- Attacker controls: the transaction witness, anchor output, and script-path structure
- Exploit idea: bind attacker-controlled the transaction witness, anchor output, and script-path structure to the wrong trusted bridge context
- Invariant to test: the raw move transaction must be structurally valid and bound to the exact deposit it finalizes
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust test that feeds malformed or stale move-to-vault transactions through the public RPC and assert the raw tx is rejected before any state mutation
