# Q3329: Reuse stale partially-completed state in send_move_to_vault_tx

## Question
Can an unprivileged attacker replay or delay the claimed `deposit_outpoint` bound to that raw move transaction so `send_move_to_vault_tx` resumes from stale partially-completed state after the canonical bridge context changed, corrupting the vault destination and anchor/output structure and breaking the invariant that the raw move transaction must be structurally valid and bound to the exact deposit it finalizes, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/rpc/aggregator.rs::send_move_to_vault_tx
- Entrypoint: public gRPC `ClementineAggregator.SendMoveToVaultTx` request with attacker-supplied raw move transaction bytes
- Attacker controls: the claimed `deposit_outpoint` bound to that raw move transaction
- Exploit idea: resume from stale partially completed state after canonical state changed via the claimed `deposit_outpoint` bound to that raw move transaction
- Invariant to test: the raw move transaction must be structurally valid and bound to the exact deposit it finalizes
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust test that feeds malformed or stale move-to-vault transactions through the public RPC and assert the raw tx is rejected before any state mutation
