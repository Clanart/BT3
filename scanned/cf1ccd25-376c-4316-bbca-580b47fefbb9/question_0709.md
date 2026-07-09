# Q709: NEAR required_balance_for_fast_transfer storage quote underestimates live state

## Question
Can an unprivileged attacker reach `internal accounting helper reached from public fast-transfer paths` and make `near/omni-bridge/src/storage.rs::required_balance_for_fast_transfer` reserve less storage than the live bridge state actually consumes because of computes storage reserved for relayer-sponsored fast transfer state, violating `fast-transfer storage quoting must not let relayers create persistent claims that underpay for their own cleanup or refund paths`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_fast_transfer`
- Entrypoint: `internal accounting helper reached from public fast-transfer paths`
- Attacker controls: fast-transfer id structure, relayer fields, and destination branch
- Exploit idea: Target helper functions that quote storage for pending transfers, finalization records, fast transfers, binds, or deployments.
- Invariant to test: fast-transfer storage quoting must not let relayers create persistent claims that underpay for their own cleanup or refund paths
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Measure storage usage across maximal inputs and assert that quoted requirements always exceed or equal the true post-state footprint.
