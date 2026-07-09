# Q578: EVM ENearProxy finaliseNearToEthTransfer replay guard can be bypassed or consumed incorrectly at boundary values

## Question
Can an unprivileged attacker trigger `public legacy proof-submission entrypoint` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `evm/src/eNear/contracts/ENearProxy.sol::finaliseNearToEthTransfer` violate `legacy proof validation must reject stale, replayed, cross-context, or partially-validated proofs that can mint eNEAR more than once` in the `replay guard can be bypassed or consumed incorrectly` attack class because delegates proof validation to `prover.proveOutcome` and then forwards the same proof into `eNear.finaliseNearToEthTransfer` becomes fragile at those edges?

## Target
- File/function: `evm/src/eNear/contracts/ENearProxy.sol::finaliseNearToEthTransfer`
- Entrypoint: `public legacy proof-submission entrypoint`
- Attacker controls: proof bytes, proof block height, and timing relative to current receipt id and pause state
- Exploit idea: Stress replay-protection state keyed only by nonce, transfer id, or bitmap position across branches and chains. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: legacy proof validation must reject stale, replayed, cross-context, or partially-validated proofs that can mint eNEAR more than once
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay valid proofs/signatures with altered non-economic fields and assert that only the exact originally-settled event is rejected as already used. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
