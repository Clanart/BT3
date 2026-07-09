# Q74: EVM ENearProxy finaliseNearToEthTransfer replay guard can be bypassed or consumed incorrectly

## Question
Can an unprivileged attacker settle through `public legacy proof-submission entrypoint` and make `evm/src/eNear/contracts/ENearProxy.sol::finaliseNearToEthTransfer` either bypass replay protection or consume it for the wrong event because of delegates proof validation to `prover.proveOutcome` and then forwards the same proof into `eNear.finaliseNearToEthTransfer`, violating `legacy proof validation must reject stale, replayed, cross-context, or partially-validated proofs that can mint eNEAR more than once`?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::finaliseNearToEthTransfer`
- Entrypoint: `public legacy proof-submission entrypoint`
- Attacker controls: proof bytes, proof block height, and timing relative to current receipt id and pause state
- Exploit idea: Stress replay-protection state keyed only by nonce, transfer id, or bitmap position across branches and chains.
- Invariant to test: legacy proof validation must reject stale, replayed, cross-context, or partially-validated proofs that can mint eNEAR more than once
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay valid proofs/signatures with altered non-economic fields and assert that only the exact originally-settled event is rejected as already used.
