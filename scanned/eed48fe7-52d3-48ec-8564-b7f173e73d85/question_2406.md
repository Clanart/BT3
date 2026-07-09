# Q2406: EVM custom-minter bridge path callback refund creates value gap through cross-module drift

## Question
Can an unprivileged attacker use `public `initTransfer` and `finTransfer` when `customMinters[token] != 0`` with control over token address, custom-minter registration state, amount, and recipient and desynchronize `evm/src/omni-bridge/contracts/OmniBridge.sol customMinters branches` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `callback refund creates value gap` attack class because delegates burn and mint semantics to an external custom minter instead of standard bridge-token logic, violating `custom-minter branches must not let a token escape custody or mint semantics that the bridge assumes for replay protection and accounting`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol customMinters branches`
- Entrypoint: `public `initTransfer` and `finTransfer` when `customMinters[token] != 0``
- Attacker controls: token address, custom-minter registration state, amount, and recipient
- Exploit idea: Target `ft_transfer_call`-style paths where refund semantics affect whether state is removed or custody is burned. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: custom-minter branches must not let a token escape custody or mint semantics that the bridge assumes for replay protection and accounting
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate every callback result and assert that no branch leaves both user-accessible funds and a still-live bridge claim for the same transfer. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridge.sol customMinters branches` and the adjacent mint, burn, or custody accounting after every branch.
