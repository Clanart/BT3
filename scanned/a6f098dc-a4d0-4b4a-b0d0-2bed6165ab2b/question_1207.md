# Q1207: NEAR required_balance_for_fast_transfer storage quote underestimates live state at boundary values

## Question
Can an unprivileged attacker trigger `internal accounting helper reached from public fast-transfer paths` with boundary-controlled inputs covering minimal deposits, maximal storage payloads, and withdrawal edges and make `near/omni-bridge/src/storage.rs::required_balance_for_fast_transfer` violate `fast-transfer storage quoting must not let relayers create persistent claims that underpay for their own cleanup or refund paths` in the `storage quote underestimates live state` attack class because computes storage reserved for relayer-sponsored fast transfer state becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_fast_transfer`
- Entrypoint: `internal accounting helper reached from public fast-transfer paths`
- Attacker controls: fast-transfer id structure, relayer fields, and destination branch
- Exploit idea: Target helper functions that quote storage for pending transfers, finalization records, fast transfers, binds, or deployments. Concentrate on minimal deposits, maximal storage payloads, and withdrawal edges.
- Invariant to test: fast-transfer storage quoting must not let relayers create persistent claims that underpay for their own cleanup or refund paths
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Measure storage usage across maximal inputs and assert that quoted requirements always exceed or equal the true post-state footprint. Sweep boundary values for minimal deposits, maximal storage payloads, and withdrawal edges and assert that the same invariant holds at every edge.
