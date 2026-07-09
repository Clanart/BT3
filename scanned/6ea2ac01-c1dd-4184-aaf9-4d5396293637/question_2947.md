# Q2947: EVM ENearProxy finaliseNearToEthTransfer signature malleability or alternate recovery through cross-module drift

## Question
Can an unprivileged attacker use `public legacy proof-submission entrypoint` with control over proof bytes, proof block height, and timing relative to current receipt id and pause state and desynchronize `evm/src/eNear/contracts/ENearProxy.sol::finaliseNearToEthTransfer` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `signature malleability or alternate recovery` attack class because delegates proof validation to `prover.proveOutcome` and then forwards the same proof into `eNear.finaliseNearToEthTransfer`, violating `legacy proof validation must reject stale, replayed, cross-context, or partially-validated proofs that can mint eNEAR more than once`?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::finaliseNearToEthTransfer`
- Entrypoint: `public legacy proof-submission entrypoint`
- Attacker controls: proof bytes, proof block height, and timing relative to current receipt id and pause state
- Exploit idea: Target `v/r/s` normalization, ECDSA recovery semantics, and Ethereum-style signature handling on non-Ethereum chains. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: legacy proof validation must reject stale, replayed, cross-context, or partially-validated proofs that can mint eNEAR more than once
- Expected Immunefi impact: Cryptographic flaws
- Fast validation: Try low-s/high-s and alternate-`v` forms and assert that recovery either rejects them or yields one unique signer and one unique message. Also assert cross-module consistency between `evm/src/eNear/contracts/ENearProxy.sol::finaliseNearToEthTransfer` and the adjacent replay-protection bookkeeping after every branch.
