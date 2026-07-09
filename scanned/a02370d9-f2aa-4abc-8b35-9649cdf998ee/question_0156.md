# Q156: EVM completedTransfers mapping replay guard can be bypassed or consumed incorrectly

## Question
Can an unprivileged attacker settle through `public EVM `finTransfer`` and make `evm/src/omni-bridge/contracts/OmniBridge.sol::completedTransfers` either bypass replay protection or consume it for the wrong event because of uses a single `mapping(uint64 => bool)` keyed only by `destinationNonce` for replay protection on inbound settlements, violating `replay protection must not treat two distinct signed events as the same because they reuse a nonce in another domain, nor let one valid event consume another’s settlement slot`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::completedTransfers`
- Entrypoint: `public EVM `finTransfer``
- Attacker controls: destination nonce values across chains and message classes
- Exploit idea: Stress replay-protection state keyed only by nonce, transfer id, or bitmap position across branches and chains.
- Invariant to test: replay protection must not treat two distinct signed events as the same because they reuse a nonce in another domain, nor let one valid event consume another’s settlement slot
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay valid proofs/signatures with altered non-economic fields and assert that only the exact originally-settled event is rejected as already used.
