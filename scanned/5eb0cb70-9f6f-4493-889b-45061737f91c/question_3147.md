# Q3147: EVM custom-minter bridge path asset-branch confusion on finalization at boundary values

## Question
Can an unprivileged attacker trigger `public `initTransfer` and `finTransfer` when `customMinters[token] != 0`` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `evm/src/omni-bridge/contracts/OmniBridge.sol customMinters branches` violate `custom-minter branches must not let a token escape custody or mint semantics that the bridge assumes for replay protection and accounting` in the `asset-branch confusion on finalization` attack class because delegates burn and mint semantics to an external custom minter instead of standard bridge-token logic becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol customMinters branches`
- Entrypoint: `public `initTransfer` and `finTransfer` when `customMinters[token] != 0``
- Attacker controls: token address, custom-minter registration state, amount, and recipient
- Exploit idea: Target native-versus-wrapped, vault-versus-mint, ERC-20-versus-ERC-1155, or custom-minter-versus-custody branches. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: custom-minter branches must not let a token escape custody or mint semantics that the bridge assumes for replay protection and accounting
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Assert that every accepted settlement lands on exactly the branch implied by the validated source asset type and mapping state. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
