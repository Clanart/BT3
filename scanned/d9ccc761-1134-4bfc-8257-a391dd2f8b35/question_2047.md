# Q2047: EVM ENearProxy finaliseNearToEthTransfer stale or reordered proof acceptance

## Question
Can an unprivileged attacker replay an older but still valid proof through `public legacy proof-submission entrypoint` and make `evm/src/eNear/contracts/ENearProxy.sol::finaliseNearToEthTransfer` treat it as fresh because of delegates proof validation to `prover.proveOutcome` and then forwards the same proof into `eNear.finaliseNearToEthTransfer`, violating `legacy proof validation must reject stale, replayed, cross-context, or partially-validated proofs that can mint eNEAR more than once`?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::finaliseNearToEthTransfer`
- Entrypoint: `public legacy proof-submission entrypoint`
- Attacker controls: proof bytes, proof block height, and timing relative to current receipt id and pause state
- Exploit idea: Focus on receipt ids, VAA sequence use, block-hash freshness, and whether replay state keys the exact economic event.
- Invariant to test: legacy proof validation must reject stale, replayed, cross-context, or partially-validated proofs that can mint eNEAR more than once
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Submit old proofs after later events and assert that replay protection and freshness checks reject them without stranding legitimate state.
