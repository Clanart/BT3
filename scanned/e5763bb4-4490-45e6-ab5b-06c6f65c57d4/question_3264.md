# Q3264: NEAR EVM eNear interface path legacy proof can be replayed in modern context

## Question
Can an unprivileged attacker submit a valid legacy proof through `legacy/public eNEAR mint/burn/finalize flows` and make `evm/src/eNear/contracts/IENear.sol and ENearProxy usage` accept it after the bridge state moved on because of legacy proxy routes proof-validated Near outcomes into an older eNEAR mint/burn interface that still interacts with live bridge state, violating `legacy adapter flows must not let stale eNEAR semantics bypass current replay protection or asset-backing assumptions`?

## Target
- File/function: `evm/src/eNear/contracts/IENear.sol and ENearProxy usage`
- Entrypoint: `legacy/public eNEAR mint/burn/finalize flows`
- Attacker controls: proof bytes, receipt ids, token address, amount, and pause state
- Exploit idea: Look for adapters that validate one older proof format but still affect live bridge state.
- Invariant to test: legacy adapter flows must not let stale eNEAR semantics bypass current replay protection or asset-backing assumptions
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Attempt stale-proof replay and assert that current bridge state or replay guards reject it once the event was consumed.
