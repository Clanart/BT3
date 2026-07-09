# Q324: EVM completedTransfers mapping replay guard can be bypassed or consumed incorrectly via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public EVM `finTransfer`` and then replay or reorder the earlier source-chain event or later forwarded bridge leg so that `evm/src/omni-bridge/contracts/OmniBridge.sol::completedTransfers` ends up accepting two inconsistent interpretations of the same economic event specifically around `replay guard can be bypassed or consumed incorrectly` under uses a single `mapping(uint64 => bool)` keyed only by `destinationNonce` for replay protection on inbound settlements, violating `replay protection must not treat two distinct signed events as the same because they reuse a nonce in another domain, nor let one valid event consume another’s settlement slot`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::completedTransfers`
- Entrypoint: `public EVM `finTransfer``
- Attacker controls: destination nonce values across chains and message classes
- Exploit idea: Stress replay-protection state keyed only by nonce, transfer id, or bitmap position across branches and chains. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: replay protection must not treat two distinct signed events as the same because they reuse a nonce in another domain, nor let one valid event consume another’s settlement slot
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay valid proofs/signatures with altered non-economic fields and assert that only the exact originally-settled event is rejected as already used. Then replay or reorder the earlier source-chain event or later forwarded bridge leg and assert that the bridge still exposes only one valid economic outcome.
