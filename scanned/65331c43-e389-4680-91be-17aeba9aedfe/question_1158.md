# Q1158: EVM completedTransfers mapping state update before full validation through cross-module drift

## Question
Can an unprivileged attacker use `public EVM `finTransfer`` with control over destination nonce values across chains and message classes and desynchronize `evm/src/omni-bridge/contracts/OmniBridge.sol::completedTransfers` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `state update before full validation` attack class because uses a single `mapping(uint64 => bool)` keyed only by `destinationNonce` for replay protection on inbound settlements, violating `replay protection must not treat two distinct signed events as the same because they reuse a nonce in another domain, nor let one valid event consume another’s settlement slot`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::completedTransfers`
- Entrypoint: `public EVM `finTransfer``
- Attacker controls: destination nonce values across chains and message classes
- Exploit idea: Look for `completed`, `finalised`, or bitmap writes that happen before every branch-specific validation step and external effect. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: replay protection must not treat two distinct signed events as the same because they reuse a nonce in another domain, nor let one valid event consume another’s settlement slot
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force later validation or delivery to fail after replay state was consumed and assert that the transfer cannot be stranded or reopened inconsistently. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridge.sol::completedTransfers` and the adjacent replay-protection bookkeeping after every branch.
