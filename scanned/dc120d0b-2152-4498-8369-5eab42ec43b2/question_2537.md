# Q2537: NEAR EVM eNear interface path one inbound event spawns multiple outbound obligations at boundary values

## Question
Can an unprivileged attacker trigger `legacy/public eNEAR mint/burn/finalize flows` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `evm/src/eNear/contracts/IENear.sol and ENearProxy usage` violate `legacy adapter flows must not let stale eNEAR semantics bypass current replay protection or asset-backing assumptions` in the `one inbound event spawns multiple outbound obligations` attack class because legacy proxy routes proof-validated Near outcomes into an older eNEAR mint/burn interface that still interacts with live bridge state becomes fragile at those edges?

## Target
- File/function: `evm/src/eNear/contracts/IENear.sol and ENearProxy usage`
- Entrypoint: `legacy/public eNEAR mint/burn/finalize flows`
- Attacker controls: proof bytes, receipt ids, token address, amount, and pause state
- Exploit idea: Focus on forward-to-other-chain branches and fast-transfer substitution where an inbound event becomes a new pending transfer. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: legacy adapter flows must not let stale eNEAR semantics bypass current replay protection or asset-backing assumptions
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Track value and replay state across both the inbound leg and the forwarded leg and assert that one source event cannot increase total outstanding claims. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
