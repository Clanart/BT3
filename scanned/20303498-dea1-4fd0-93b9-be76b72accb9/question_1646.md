# Q1646: EVM completedTransfers mapping bitmap slot boundary corrupts replay protection via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public EVM `finTransfer`` and then replay or reorder the earlier source-chain event or later forwarded bridge leg so that `evm/src/omni-bridge/contracts/OmniBridge.sol::completedTransfers` ends up accepting two inconsistent interpretations of the same economic event specifically around `bitmap slot boundary corrupts replay protection` under uses a single `mapping(uint64 => bool)` keyed only by `destinationNonce` for replay protection on inbound settlements, violating `replay protection must not treat two distinct signed events as the same because they reuse a nonce in another domain, nor let one valid event consume another’s settlement slot`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::completedTransfers`
- Entrypoint: `public EVM `finTransfer``
- Attacker controls: destination nonce values across chains and message classes
- Exploit idea: Probe nonces around `250/251/252`, zero, and max `u64` values in the Starknet bitmap scheme. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: replay protection must not treat two distinct signed events as the same because they reuse a nonce in another domain, nor let one valid event consume another’s settlement slot
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Set and query boundary nonces and assert that each write flips exactly one intended replay bit. Then replay or reorder the earlier source-chain event or later forwarded bridge leg and assert that the bridge still exposes only one valid economic outcome.
