# Q1728: EVM ENearProxy finaliseNearToEthTransfer missing chain or contract domain separation through cross-module drift

## Question
Can an unprivileged attacker use `public legacy proof-submission entrypoint` with control over proof bytes, proof block height, and timing relative to current receipt id and pause state and desynchronize `evm/src/eNear/contracts/ENearProxy.sol::finaliseNearToEthTransfer` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `missing chain or contract domain separation` attack class because delegates proof validation to `prover.proveOutcome` and then forwards the same proof into `eNear.finaliseNearToEthTransfer`, violating `legacy proof validation must reject stale, replayed, cross-context, or partially-validated proofs that can mint eNEAR more than once`?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::finaliseNearToEthTransfer`
- Entrypoint: `public legacy proof-submission entrypoint`
- Attacker controls: proof bytes, proof block height, and timing relative to current receipt id and pause state
- Exploit idea: Target validators keyed by derived signer, block hash, emitter address, or payload bytes that omit some domain field. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: legacy proof validation must reject stale, replayed, cross-context, or partially-validated proofs that can mint eNEAR more than once
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Attempt cross-chain and cross-contract replay of the same validated bytes and assert that every trust domain field participates in acceptance. Also assert cross-module consistency between `evm/src/eNear/contracts/ENearProxy.sol::finaliseNearToEthTransfer` and the adjacent replay-protection bookkeeping after every branch.
