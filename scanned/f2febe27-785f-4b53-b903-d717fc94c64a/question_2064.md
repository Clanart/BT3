# Q2064: Starknet fin_transfer asset-branch confusion on finalization

## Question
Can an unprivileged attacker use `public Starknet settlement entrypoint` to make `starknet/src/omni_bridge.cairo::fin_transfer` release value through a more favorable branch than the source event actually authorized because of checks pause flags, enforces `!is_transfer_finalised(destination_nonce)`, marks the nonce finalised, verifies the signed Borsh payload, and then releases native or bridge-token value, violating `a signed inbound settlement must never be replayable, branch-switchable, or capable of failing after finalisation state changes in a way that strands or duplicates funds`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::fin_transfer`
- Entrypoint: `public Starknet settlement entrypoint`
- Attacker controls: signature fields, destination nonce, origin chain, origin nonce, token address, amount, recipient, fee recipient, and message
- Exploit idea: Target native-versus-wrapped, vault-versus-mint, ERC-20-versus-ERC-1155, or custom-minter-versus-custody branches.
- Invariant to test: a signed inbound settlement must never be replayable, branch-switchable, or capable of failing after finalisation state changes in a way that strands or duplicates funds
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Assert that every accepted settlement lands on exactly the branch implied by the validated source asset type and mapping state.
