# Q97: Starknet BridgeToken mint asset-branch confusion on finalization

## Question
Can an unprivileged attacker use `public settlement-side mint path reached from `fin_transfer`` to make `starknet/src/bridge_token.cairo::mint` release value through a more favorable branch than the source event actually authorized because of mints wrapped supply into the recipient account under control of the omni bridge, violating `minted supply must only arise from one validated settlement event and must not survive a later rollback or replay edge case`?

## Target
- File/function: `starknet/src/bridge_token.cairo::mint`
- Entrypoint: `public settlement-side mint path reached from `fin_transfer``
- Attacker controls: recipient address, amount, and any receiver-side behavior after receiving bridged tokens
- Exploit idea: Target native-versus-wrapped, vault-versus-mint, ERC-20-versus-ERC-1155, or custom-minter-versus-custody branches.
- Invariant to test: minted supply must only arise from one validated settlement event and must not survive a later rollback or replay edge case
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Assert that every accepted settlement lands on exactly the branch implied by the validated source asset type and mapping state.
