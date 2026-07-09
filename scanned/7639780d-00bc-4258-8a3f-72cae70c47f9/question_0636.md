# Q636: EVM custom-minter bridge path burn or lock before irreversible state at boundary values

## Question
Can an unprivileged attacker trigger `public `initTransfer` and `finTransfer` when `customMinters[token] != 0`` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `evm/src/omni-bridge/contracts/OmniBridge.sol customMinters branches` violate `custom-minter branches must not let a token escape custody or mint semantics that the bridge assumes for replay protection and accounting` in the `burn or lock before irreversible state` attack class because delegates burn and mint semantics to an external custom minter instead of standard bridge-token logic becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol customMinters branches`
- Entrypoint: `public `initTransfer` and `finTransfer` when `customMinters[token] != 0``
- Attacker controls: token address, custom-minter registration state, amount, and recipient
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: custom-minter branches must not let a token escape custody or mint semantics that the bridge assumes for replay protection and accounting
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
