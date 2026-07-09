# Q1463: EVM custom-minter bridge path native versus wrapped branch switch

## Question
Can an unprivileged attacker choose inputs to `public `initTransfer` and `finTransfer` when `customMinters[token] != 0`` that make `evm/src/omni-bridge/contracts/OmniBridge.sol customMinters branches` classify the asset differently before and after a custody-changing step through delegates burn and mint semantics to an external custom minter instead of standard bridge-token logic, violating `custom-minter branches must not let a token escape custody or mint semantics that the bridge assumes for replay protection and accounting`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol customMinters branches`
- Entrypoint: `public `initTransfer` and `finTransfer` when `customMinters[token] != 0``
- Attacker controls: token address, custom-minter registration state, amount, and recipient
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates.
- Invariant to test: custom-minter branches must not let a token escape custody or mint semantics that the bridge assumes for replay protection and accounting
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models.
