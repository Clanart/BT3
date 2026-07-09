# Q3669: NEAR EVM eNear interface path legacy proof can be replayed in modern context at boundary values

## Question
Can an unprivileged attacker trigger `legacy/public eNEAR mint/burn/finalize flows` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `evm/src/eNear/contracts/IENear.sol and ENearProxy usage` violate `legacy adapter flows must not let stale eNEAR semantics bypass current replay protection or asset-backing assumptions` in the `legacy proof can be replayed in modern context` attack class because legacy proxy routes proof-validated Near outcomes into an older eNEAR mint/burn interface that still interacts with live bridge state becomes fragile at those edges?

## Target
- File/function: `evm/src/eNear/contracts/IENear.sol and ENearProxy usage`
- Entrypoint: `legacy/public eNEAR mint/burn/finalize flows`
- Attacker controls: proof bytes, receipt ids, token address, amount, and pause state
- Exploit idea: Look for adapters that validate one older proof format but still affect live bridge state. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: legacy adapter flows must not let stale eNEAR semantics bypass current replay protection or asset-backing assumptions
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Attempt stale-proof replay and assert that current bridge state or replay guards reject it once the event was consumed. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
