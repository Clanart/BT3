# Q3688: EVM custom-minter bridge path delivery callback leaves inconsistent state at boundary values

## Question
Can an unprivileged attacker trigger `public `initTransfer` and `finTransfer` when `customMinters[token] != 0`` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `evm/src/omni-bridge/contracts/OmniBridge.sol customMinters branches` violate `custom-minter branches must not let a token escape custody or mint semantics that the bridge assumes for replay protection and accounting` in the `delivery callback leaves inconsistent state` attack class because delegates burn and mint semantics to an external custom minter instead of standard bridge-token logic becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol customMinters branches`
- Entrypoint: `public `initTransfer` and `finTransfer` when `customMinters[token] != 0``
- Attacker controls: token address, custom-minter registration state, amount, and recipient
- Exploit idea: Focus on `ft_transfer_call`, unwrap callbacks, and post-delivery resolution that decide whether to burn, refund, or remove records. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: custom-minter branches must not let a token escape custody or mint semantics that the bridge assumes for replay protection and accounting
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Enumerate callback results and assert that each result maps to exactly one consistent combination of delivered value, replay state, and storage refund. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
