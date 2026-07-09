# Q3817: EVM custom-minter bridge path one inbound event spawns multiple outbound obligations

## Question
Can an unprivileged attacker settle through `public `initTransfer` and `finTransfer` when `customMinters[token] != 0`` and make `evm/src/omni-bridge/contracts/OmniBridge.sol customMinters branches` both release local value and create a second valid outbound bridge obligation via delegates burn and mint semantics to an external custom minter instead of standard bridge-token logic, violating `custom-minter branches must not let a token escape custody or mint semantics that the bridge assumes for replay protection and accounting`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol customMinters branches`
- Entrypoint: `public `initTransfer` and `finTransfer` when `customMinters[token] != 0``
- Attacker controls: token address, custom-minter registration state, amount, and recipient
- Exploit idea: Focus on forward-to-other-chain branches and fast-transfer substitution where an inbound event becomes a new pending transfer.
- Invariant to test: custom-minter branches must not let a token escape custody or mint semantics that the bridge assumes for replay protection and accounting
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Track value and replay state across both the inbound leg and the forwarded leg and assert that one source event cannot increase total outstanding claims.
