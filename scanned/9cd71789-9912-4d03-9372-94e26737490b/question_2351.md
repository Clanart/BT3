# Q2351: EVM ENearProxy finaliseNearToEthTransfer stale or reordered proof acceptance through cross-module drift

## Question
Can an unprivileged attacker use `public legacy proof-submission entrypoint` with control over proof bytes, proof block height, and timing relative to current receipt id and pause state and desynchronize `evm/src/eNear/contracts/ENearProxy.sol::finaliseNearToEthTransfer` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `stale or reordered proof acceptance` attack class because delegates proof validation to `prover.proveOutcome` and then forwards the same proof into `eNear.finaliseNearToEthTransfer`, violating `legacy proof validation must reject stale, replayed, cross-context, or partially-validated proofs that can mint eNEAR more than once`?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::finaliseNearToEthTransfer`
- Entrypoint: `public legacy proof-submission entrypoint`
- Attacker controls: proof bytes, proof block height, and timing relative to current receipt id and pause state
- Exploit idea: Focus on receipt ids, VAA sequence use, block-hash freshness, and whether replay state keys the exact economic event. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: legacy proof validation must reject stale, replayed, cross-context, or partially-validated proofs that can mint eNEAR more than once
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Submit old proofs after later events and assert that replay protection and freshness checks reject them without stranding legitimate state. Also assert cross-module consistency between `evm/src/eNear/contracts/ENearProxy.sol::finaliseNearToEthTransfer` and the adjacent replay-protection bookkeeping after every branch.
