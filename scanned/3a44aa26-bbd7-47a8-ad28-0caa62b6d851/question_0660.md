# Q660: EVM completedTransfers mapping replay guard can be bypassed or consumed incorrectly at boundary values

## Question
Can an unprivileged attacker trigger `public EVM `finTransfer`` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `evm/src/omni-bridge/contracts/OmniBridge.sol::completedTransfers` violate `replay protection must not treat two distinct signed events as the same because they reuse a nonce in another domain, nor let one valid event consume another’s settlement slot` in the `replay guard can be bypassed or consumed incorrectly` attack class because uses a single `mapping(uint64 => bool)` keyed only by `destinationNonce` for replay protection on inbound settlements becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::completedTransfers`
- Entrypoint: `public EVM `finTransfer``
- Attacker controls: destination nonce values across chains and message classes
- Exploit idea: Stress replay-protection state keyed only by nonce, transfer id, or bitmap position across branches and chains. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: replay protection must not treat two distinct signed events as the same because they reuse a nonce in another domain, nor let one valid event consume another’s settlement slot
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay valid proofs/signatures with altered non-economic fields and assert that only the exact originally-settled event is rejected as already used. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
