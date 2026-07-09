# Q2081: NEAR EVM eNear interface path one inbound event spawns multiple outbound obligations

## Question
Can an unprivileged attacker settle through `legacy/public eNEAR mint/burn/finalize flows` and make `evm/src/eNear/contracts/IENear.sol and ENearProxy usage` both release local value and create a second valid outbound bridge obligation via legacy proxy routes proof-validated Near outcomes into an older eNEAR mint/burn interface that still interacts with live bridge state, violating `legacy adapter flows must not let stale eNEAR semantics bypass current replay protection or asset-backing assumptions`?

## Target
- File/function: `evm/src/eNear/contracts/IENear.sol and ENearProxy usage`
- Entrypoint: `legacy/public eNEAR mint/burn/finalize flows`
- Attacker controls: proof bytes, receipt ids, token address, amount, and pause state
- Exploit idea: Focus on forward-to-other-chain branches and fast-transfer substitution where an inbound event becomes a new pending transfer.
- Invariant to test: legacy adapter flows must not let stale eNEAR semantics bypass current replay protection or asset-backing assumptions
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Track value and replay state across both the inbound leg and the forwarded leg and assert that one source event cannot increase total outstanding claims.
