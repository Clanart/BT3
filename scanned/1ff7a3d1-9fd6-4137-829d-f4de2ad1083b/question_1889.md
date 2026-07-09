# Q1889: EVM ENearProxy finaliseNearToEthTransfer missing chain or contract domain separation at boundary values

## Question
Can an unprivileged attacker trigger `public legacy proof-submission entrypoint` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `evm/src/eNear/contracts/ENearProxy.sol::finaliseNearToEthTransfer` violate `legacy proof validation must reject stale, replayed, cross-context, or partially-validated proofs that can mint eNEAR more than once` in the `missing chain or contract domain separation` attack class because delegates proof validation to `prover.proveOutcome` and then forwards the same proof into `eNear.finaliseNearToEthTransfer` becomes fragile at those edges?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::finaliseNearToEthTransfer`
- Entrypoint: `public legacy proof-submission entrypoint`
- Attacker controls: proof bytes, proof block height, and timing relative to current receipt id and pause state
- Exploit idea: Target validators keyed by derived signer, block hash, emitter address, or payload bytes that omit some domain field. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: legacy proof validation must reject stale, replayed, cross-context, or partially-validated proofs that can mint eNEAR more than once
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Attempt cross-chain and cross-contract replay of the same validated bytes and assert that every trust domain field participates in acceptance. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
