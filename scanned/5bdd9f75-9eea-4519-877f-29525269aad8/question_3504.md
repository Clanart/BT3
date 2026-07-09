# Q3504: EVM ENearProxy finaliseNearToEthTransfer shared proof response reused across entrypoints through cross-module drift

## Question
Can an unprivileged attacker use `public legacy proof-submission entrypoint` with control over proof bytes, proof block height, and timing relative to current receipt id and pause state and desynchronize `evm/src/eNear/contracts/ENearProxy.sol::finaliseNearToEthTransfer` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `shared proof response reused across entrypoints` attack class because delegates proof validation to `prover.proveOutcome` and then forwards the same proof into `eNear.finaliseNearToEthTransfer`, violating `legacy proof validation must reject stale, replayed, cross-context, or partially-validated proofs that can mint eNEAR more than once`?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::finaliseNearToEthTransfer`
- Entrypoint: `public legacy proof-submission entrypoint`
- Attacker controls: proof bytes, proof block height, and timing relative to current receipt id and pause state
- Exploit idea: Attack systems where one verifier contract serves deploy, finalize, metadata, and fee-claim flows with a shared result type. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: legacy proof validation must reject stale, replayed, cross-context, or partially-validated proofs that can mint eNEAR more than once
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Attempt to route accepted verifier outputs into every public proof consumer and assert that each entrypoint only accepts its intended result variant and source semantics. Also assert cross-module consistency between `evm/src/eNear/contracts/ENearProxy.sol::finaliseNearToEthTransfer` and the adjacent replay-protection bookkeeping after every branch.
