# Q3534: NEAR EVM eNear interface path legacy proof can be replayed in modern context through cross-module drift

## Question
Can an unprivileged attacker use `legacy/public eNEAR mint/burn/finalize flows` with control over proof bytes, receipt ids, token address, amount, and pause state and desynchronize `evm/src/eNear/contracts/IENear.sol and ENearProxy usage` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `legacy proof can be replayed in modern context` attack class because legacy proxy routes proof-validated Near outcomes into an older eNEAR mint/burn interface that still interacts with live bridge state, violating `legacy adapter flows must not let stale eNEAR semantics bypass current replay protection or asset-backing assumptions`?

## Target
- File/function: `evm/src/eNear/contracts/IENear.sol and ENearProxy usage`
- Entrypoint: `legacy/public eNEAR mint/burn/finalize flows`
- Attacker controls: proof bytes, receipt ids, token address, amount, and pause state
- Exploit idea: Look for adapters that validate one older proof format but still affect live bridge state. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: legacy adapter flows must not let stale eNEAR semantics bypass current replay protection or asset-backing assumptions
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Attempt stale-proof replay and assert that current bridge state or replay guards reject it once the event was consumed. Also assert cross-module consistency between `evm/src/eNear/contracts/IENear.sol and ENearProxy usage` and the adjacent mint, burn, or custody accounting after every branch.
