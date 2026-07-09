# Q3399: NEAR EVM eNear interface path legacy proof can be replayed in modern context via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `legacy/public eNEAR mint/burn/finalize flows` and then replay or reorder another proof-consuming public entrypoint so that `evm/src/eNear/contracts/IENear.sol and ENearProxy usage` ends up accepting two inconsistent interpretations of the same economic event specifically around `legacy proof can be replayed in modern context` under legacy proxy routes proof-validated Near outcomes into an older eNEAR mint/burn interface that still interacts with live bridge state, violating `legacy adapter flows must not let stale eNEAR semantics bypass current replay protection or asset-backing assumptions`?

## Target
- File/function: `evm/src/eNear/contracts/IENear.sol and ENearProxy usage`
- Entrypoint: `legacy/public eNEAR mint/burn/finalize flows`
- Attacker controls: proof bytes, receipt ids, token address, amount, and pause state
- Exploit idea: Look for adapters that validate one older proof format but still affect live bridge state. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: legacy adapter flows must not let stale eNEAR semantics bypass current replay protection or asset-backing assumptions
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Attempt stale-proof replay and assert that current bridge state or replay guards reject it once the event was consumed. Then replay or reorder another proof-consuming public entrypoint and assert that the bridge still exposes only one valid economic outcome.
