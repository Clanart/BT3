# Q3306: Reuse stale partially-completed state in aggregator_two_deposit_movetx_and_emergency_stop

## Question
Can an unprivileged attacker replay or delay the timing of the call relative to an in-flight or partially failed deposit finalization so `aggregator_two_deposit_movetx_and_emergency_stop` resumes from stale partially-completed state after the canonical bridge context changed, corrupting the vault destination and anchor/output structure and breaking the invariant that partial finalization state must not let an attacker inject a different vault spend path, leading to High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed?

## Target
- File/function: core/src/rpc/aggregator.rs::aggregator_two_deposit_movetx_and_emergency_stop
- Entrypoint: public gRPC `ClementineAggregator.SendMoveToVaultTx` request with attacker-supplied raw move transaction bytes
- Attacker controls: the timing of the call relative to an in-flight or partially failed deposit finalization
- Exploit idea: resume from stale partially completed state after canonical state changed via the timing of the call relative to an in-flight or partially failed deposit finalization
- Invariant to test: partial finalization state must not let an attacker inject a different vault spend path
- Expected Immunefi impact: High. Bugs with Clementine presigning, causing failure of operator’s reimbursements failed
- Fast validation: add a Rust test that feeds malformed or stale move-to-vault transactions through the public RPC and assert the raw tx is rejected before any state mutation
