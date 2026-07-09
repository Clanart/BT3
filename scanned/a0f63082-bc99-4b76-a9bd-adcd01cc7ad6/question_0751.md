# Q751: Solana finalize_transfer_sol asset-branch confusion on finalization

## Question
Can an unprivileged attacker use `public Solana `finalize_transfer_sol` instruction` to make `solana/programs/bridge_token_factory/src/lib.rs::finalize_transfer_sol` release value through a more favorable branch than the source event actually authorized because of verifies the NEAR-derived signature for a SOL transfer, uses nonce tracking, and processes native-SOL finalization, violating `native-SOL settlement must not be replayable or capable of releasing value under the wrong domain, amount, or recipient binding`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::finalize_transfer_sol`
- Entrypoint: `public Solana `finalize_transfer_sol` instruction`
- Attacker controls: signed payload bytes, destination nonce, recipient account, payer funding, and SOL-specific account layout
- Exploit idea: Target native-versus-wrapped, vault-versus-mint, ERC-20-versus-ERC-1155, or custom-minter-versus-custody branches.
- Invariant to test: native-SOL settlement must not be replayable or capable of releasing value under the wrong domain, amount, or recipient binding
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Assert that every accepted settlement lands on exactly the branch implied by the validated source asset type and mapping state.
