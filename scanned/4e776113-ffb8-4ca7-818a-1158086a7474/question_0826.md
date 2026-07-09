# Q826: EVM completedTransfers mapping state update before full validation

## Question
Can an unprivileged attacker exploit `public EVM `finTransfer`` so that `evm/src/omni-bridge/contracts/OmniBridge.sol::completedTransfers` mutates finalization state before all signature or proof checks implied by uses a single `mapping(uint64 => bool)` keyed only by `destinationNonce` for replay protection on inbound settlements are complete, violating `replay protection must not treat two distinct signed events as the same because they reuse a nonce in another domain, nor let one valid event consume another’s settlement slot`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::completedTransfers`
- Entrypoint: `public EVM `finTransfer``
- Attacker controls: destination nonce values across chains and message classes
- Exploit idea: Look for `completed`, `finalised`, or bitmap writes that happen before every branch-specific validation step and external effect.
- Invariant to test: replay protection must not treat two distinct signed events as the same because they reuse a nonce in another domain, nor let one valid event consume another’s settlement slot
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force later validation or delivery to fail after replay state was consumed and assert that the transfer cannot be stranded or reopened inconsistently.
