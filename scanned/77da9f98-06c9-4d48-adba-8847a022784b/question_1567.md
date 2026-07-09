# Q1567: EVM ENearProxy finaliseNearToEthTransfer missing chain or contract domain separation via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public legacy proof-submission entrypoint` and then replay or reorder another proof-consuming public entrypoint so that `evm/src/eNear/contracts/ENearProxy.sol::finaliseNearToEthTransfer` ends up accepting two inconsistent interpretations of the same economic event specifically around `missing chain or contract domain separation` under delegates proof validation to `prover.proveOutcome` and then forwards the same proof into `eNear.finaliseNearToEthTransfer`, violating `legacy proof validation must reject stale, replayed, cross-context, or partially-validated proofs that can mint eNEAR more than once`?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::finaliseNearToEthTransfer`
- Entrypoint: `public legacy proof-submission entrypoint`
- Attacker controls: proof bytes, proof block height, and timing relative to current receipt id and pause state
- Exploit idea: Target validators keyed by derived signer, block hash, emitter address, or payload bytes that omit some domain field. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: legacy proof validation must reject stale, replayed, cross-context, or partially-validated proofs that can mint eNEAR more than once
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Attempt cross-chain and cross-contract replay of the same validated bytes and assert that every trust domain field participates in acceptance. Then replay or reorder another proof-consuming public entrypoint and assert that the bridge still exposes only one valid economic outcome.
