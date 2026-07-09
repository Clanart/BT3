# Q2706: EVM custom-minter bridge path asset-branch confusion on finalization

## Question
Can an unprivileged attacker use `public `initTransfer` and `finTransfer` when `customMinters[token] != 0`` to make `evm/src/omni-bridge/contracts/OmniBridge.sol customMinters branches` release value through a more favorable branch than the source event actually authorized because of delegates burn and mint semantics to an external custom minter instead of standard bridge-token logic, violating `custom-minter branches must not let a token escape custody or mint semantics that the bridge assumes for replay protection and accounting`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol customMinters branches`
- Entrypoint: `public `initTransfer` and `finTransfer` when `customMinters[token] != 0``
- Attacker controls: token address, custom-minter registration state, amount, and recipient
- Exploit idea: Target native-versus-wrapped, vault-versus-mint, ERC-20-versus-ERC-1155, or custom-minter-versus-custody branches.
- Invariant to test: custom-minter branches must not let a token escape custody or mint semantics that the bridge assumes for replay protection and accounting
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Assert that every accepted settlement lands on exactly the branch implied by the validated source asset type and mapping state.
