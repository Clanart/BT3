# Q2558: EVM custom-minter bridge path callback refund creates value gap at boundary values

## Question
Can an unprivileged attacker trigger `public `initTransfer` and `finTransfer` when `customMinters[token] != 0`` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `evm/src/omni-bridge/contracts/OmniBridge.sol customMinters branches` violate `custom-minter branches must not let a token escape custody or mint semantics that the bridge assumes for replay protection and accounting` in the `callback refund creates value gap` attack class because delegates burn and mint semantics to an external custom minter instead of standard bridge-token logic becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol customMinters branches`
- Entrypoint: `public `initTransfer` and `finTransfer` when `customMinters[token] != 0``
- Attacker controls: token address, custom-minter registration state, amount, and recipient
- Exploit idea: Target `ft_transfer_call`-style paths where refund semantics affect whether state is removed or custody is burned. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: custom-minter branches must not let a token escape custody or mint semantics that the bridge assumes for replay protection and accounting
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate every callback result and assert that no branch leaves both user-accessible funds and a still-live bridge claim for the same transfer. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
