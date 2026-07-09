# Q1485: EVM completedTransfers mapping bitmap slot boundary corrupts replay protection

## Question
Can an unprivileged attacker use `public EVM `finTransfer`` with boundary nonce values so that `evm/src/omni-bridge/contracts/OmniBridge.sol::completedTransfers` aliases or mis-marks neighboring Starknet nonces because of uses a single `mapping(uint64 => bool)` keyed only by `destinationNonce` for replay protection on inbound settlements, violating `replay protection must not treat two distinct signed events as the same because they reuse a nonce in another domain, nor let one valid event consume another’s settlement slot`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::completedTransfers`
- Entrypoint: `public EVM `finTransfer``
- Attacker controls: destination nonce values across chains and message classes
- Exploit idea: Probe nonces around `250/251/252`, zero, and max `u64` values in the Starknet bitmap scheme.
- Invariant to test: replay protection must not treat two distinct signed events as the same because they reuse a nonce in another domain, nor let one valid event consume another’s settlement slot
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Set and query boundary nonces and assert that each write flips exactly one intended replay bit.
