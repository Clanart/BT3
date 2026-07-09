# Q3126: NEAR EVM eNear interface path shared proof response reused across entrypoints at boundary values

## Question
Can an unprivileged attacker trigger `legacy/public eNEAR mint/burn/finalize flows` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `evm/src/eNear/contracts/IENear.sol and ENearProxy usage` violate `legacy adapter flows must not let stale eNEAR semantics bypass current replay protection or asset-backing assumptions` in the `shared proof response reused across entrypoints` attack class because legacy proxy routes proof-validated Near outcomes into an older eNEAR mint/burn interface that still interacts with live bridge state becomes fragile at those edges?

## Target
- File/function: `evm/src/eNear/contracts/IENear.sol and ENearProxy usage`
- Entrypoint: `legacy/public eNEAR mint/burn/finalize flows`
- Attacker controls: proof bytes, receipt ids, token address, amount, and pause state
- Exploit idea: Attack systems where one verifier contract serves deploy, finalize, metadata, and fee-claim flows with a shared result type. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: legacy adapter flows must not let stale eNEAR semantics bypass current replay protection or asset-backing assumptions
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Attempt to route accepted verifier outputs into every public proof consumer and assert that each entrypoint only accepts its intended result variant and source semantics. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
