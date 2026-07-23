# Q967: Reuse stale partially-completed state in create_movetx

## Question
Can an unprivileged attacker replay or delay the raw move-to-vault transaction bytes so `create_movetx` resumes from stale partially-completed state after the canonical bridge context changed, corrupting the move-to-vault transaction accepted for a deposit and breaking the invariant that partial finalization state must not let an attacker inject a different vault spend path, leading to High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed?

## Target
- File/function: core/src/rpc/aggregator.rs::create_movetx
- Entrypoint: public gRPC `ClementineAggregator.SendMoveToVaultTx` request with attacker-supplied raw move transaction bytes
- Attacker controls: the raw move-to-vault transaction bytes
- Exploit idea: resume from stale partially completed state after canonical state changed via the raw move-to-vault transaction bytes
- Invariant to test: partial finalization state must not let an attacker inject a different vault spend path
- Expected Immunefi impact: High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed
- Fast validation: add a Rust test that feeds malformed or stale move-to-vault transactions through the public RPC and assert the raw tx is rejected before any state mutation
