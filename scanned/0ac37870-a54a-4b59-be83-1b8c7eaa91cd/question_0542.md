# Q542: NEAR required_balance_for_fast_transfer fast-transfer storage refund reaches wrong party at boundary values

## Question
Can an unprivileged attacker trigger `internal accounting helper reached from public fast-transfer paths` with boundary-controlled inputs covering minimal deposits, maximal storage payloads, and withdrawal edges and make `near/omni-bridge/src/storage.rs::required_balance_for_fast_transfer` violate `fast-transfer storage quoting must not let relayers create persistent claims that underpay for their own cleanup or refund paths` in the `fast-transfer storage refund reaches wrong party` attack class because computes storage reserved for relayer-sponsored fast transfer state becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_fast_transfer`
- Entrypoint: `internal accounting helper reached from public fast-transfer paths`
- Attacker controls: fast-transfer id structure, relayer fields, and destination branch
- Exploit idea: Target stored `storage_owner` values and removal paths that issue refunds after relayer activity. Concentrate on minimal deposits, maximal storage payloads, and withdrawal edges.
- Invariant to test: fast-transfer storage quoting must not let relayers create persistent claims that underpay for their own cleanup or refund paths
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Create relayer and user combinations and assert that every refund lands on the exact payer who financed that fast-transfer slot. Sweep boundary values for minimal deposits, maximal storage payloads, and withdrawal edges and assert that the same invariant holds at every edge.
