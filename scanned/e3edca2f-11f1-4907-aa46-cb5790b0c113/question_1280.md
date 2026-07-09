# Q1280: NEAR EVM eNear interface path legacy or migration path aliasing at boundary values

## Question
Can an unprivileged attacker trigger `legacy/public eNEAR mint/burn/finalize flows` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `evm/src/eNear/contracts/IENear.sol and ENearProxy usage` violate `legacy adapter flows must not let stale eNEAR semantics bypass current replay protection or asset-backing assumptions` in the `legacy or migration path aliasing` attack class because legacy proxy routes proof-validated Near outcomes into an older eNEAR mint/burn interface that still interacts with live bridge state becomes fragile at those edges?

## Target
- File/function: `evm/src/eNear/contracts/IENear.sol and ENearProxy usage`
- Entrypoint: `legacy/public eNEAR mint/burn/finalize flows`
- Attacker controls: proof bytes, receipt ids, token address, amount, and pause state
- Exploit idea: Use memo-triggered legacy paths, migrated-token aliases, or old/new token relationships to create a second valid outbound interpretation of the same balance change. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: legacy adapter flows must not let stale eNEAR semantics bypass current replay protection or asset-backing assumptions
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Exercise both the modern and legacy branches with equivalent economic inputs and assert that only one bridge claim can arise from one unit of consumed value. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
