# Q1807: EVM completedTransfers mapping bitmap slot boundary corrupts replay protection through cross-module drift

## Question
Can an unprivileged attacker use `public EVM `finTransfer`` with control over destination nonce values across chains and message classes and desynchronize `evm/src/omni-bridge/contracts/OmniBridge.sol::completedTransfers` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `bitmap slot boundary corrupts replay protection` attack class because uses a single `mapping(uint64 => bool)` keyed only by `destinationNonce` for replay protection on inbound settlements, violating `replay protection must not treat two distinct signed events as the same because they reuse a nonce in another domain, nor let one valid event consume another’s settlement slot`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::completedTransfers`
- Entrypoint: `public EVM `finTransfer``
- Attacker controls: destination nonce values across chains and message classes
- Exploit idea: Probe nonces around `250/251/252`, zero, and max `u64` values in the Starknet bitmap scheme. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: replay protection must not treat two distinct signed events as the same because they reuse a nonce in another domain, nor let one valid event consume another’s settlement slot
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Set and query boundary nonces and assert that each write flips exactly one intended replay bit. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridge.sol::completedTransfers` and the adjacent replay-protection bookkeeping after every branch.
