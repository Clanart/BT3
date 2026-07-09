# Q3773: EVM ENearProxy finaliseNearToEthTransfer legacy receipt or origin counter progression is forgeable

## Question
Can an unprivileged attacker use `public legacy proof-submission entrypoint` so that `evm/src/eNear/contracts/ENearProxy.sol::finaliseNearToEthTransfer` advances or assumes a legacy receipt/origin counter in a way that authorizes multiple settlements for one event, violating `legacy proof validation must reject stale, replayed, cross-context, or partially-validated proofs that can mint eNEAR more than once`?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::finaliseNearToEthTransfer`
- Entrypoint: `public legacy proof-submission entrypoint`
- Attacker controls: proof bytes, proof block height, and timing relative to current receipt id and pause state
- Exploit idea: Target synthetic receipt ids and legacy bridges that increment counters locally before proving uniqueness elsewhere.
- Invariant to test: legacy proof validation must reject stale, replayed, cross-context, or partially-validated proofs that can mint eNEAR more than once
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay or reorder legacy settlements and assert that no synthetic or local counter can replace canonical event uniqueness.
