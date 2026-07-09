# Q3899: EVM ENearProxy finaliseNearToEthTransfer legacy receipt or origin counter progression is forgeable via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public legacy proof-submission entrypoint` and then replay or reorder another proof-consuming public entrypoint so that `evm/src/eNear/contracts/ENearProxy.sol::finaliseNearToEthTransfer` ends up accepting two inconsistent interpretations of the same economic event specifically around `legacy receipt or origin counter progression is forgeable` under delegates proof validation to `prover.proveOutcome` and then forwards the same proof into `eNear.finaliseNearToEthTransfer`, violating `legacy proof validation must reject stale, replayed, cross-context, or partially-validated proofs that can mint eNEAR more than once`?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::finaliseNearToEthTransfer`
- Entrypoint: `public legacy proof-submission entrypoint`
- Attacker controls: proof bytes, proof block height, and timing relative to current receipt id and pause state
- Exploit idea: Target synthetic receipt ids and legacy bridges that increment counters locally before proving uniqueness elsewhere. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: legacy proof validation must reject stale, replayed, cross-context, or partially-validated proofs that can mint eNEAR more than once
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay or reorder legacy settlements and assert that no synthetic or local counter can replace canonical event uniqueness. Then replay or reorder another proof-consuming public entrypoint and assert that the bridge still exposes only one valid economic outcome.
