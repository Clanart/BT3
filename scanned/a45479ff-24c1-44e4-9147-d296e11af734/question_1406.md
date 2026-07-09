# Q1406: EVM ENearProxy finaliseNearToEthTransfer missing chain or contract domain separation

## Question
Can an unprivileged attacker reuse a valid proof or signature from one chain, contract, or message domain in `public legacy proof-submission entrypoint` because `evm/src/eNear/contracts/ENearProxy.sol::finaliseNearToEthTransfer` relies on delegates proof validation to `prover.proveOutcome` and then forwards the same proof into `eNear.finaliseNearToEthTransfer` more narrowly than the true trust domain, violating `legacy proof validation must reject stale, replayed, cross-context, or partially-validated proofs that can mint eNEAR more than once`?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::finaliseNearToEthTransfer`
- Entrypoint: `public legacy proof-submission entrypoint`
- Attacker controls: proof bytes, proof block height, and timing relative to current receipt id and pause state
- Exploit idea: Target validators keyed by derived signer, block hash, emitter address, or payload bytes that omit some domain field.
- Invariant to test: legacy proof validation must reject stale, replayed, cross-context, or partially-validated proofs that can mint eNEAR more than once
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Attempt cross-chain and cross-contract replay of the same validated bytes and assert that every trust domain field participates in acceptance.
