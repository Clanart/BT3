# Q3778: Solana init_transfer same fee collectible twice

## Question
Can an unprivileged attacker reach `public Solana `init_transfer` instruction` and make `solana/programs/bridge_token_factory/src/lib.rs::init_transfer` remove or preserve fee-bearing state in a way that allows the same fee to be claimed twice, violating `one outbound Solana transfer must consume exactly the asset and fee implied by the emitted message and must not let native-vault and bridged-burn branches diverge`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::init_transfer`
- Entrypoint: `public Solana `init_transfer` instruction`
- Attacker controls: mint, source token account, optional vault, user signer, amount, fee, native fee, recipient string, and message
- Exploit idea: Target pending-transfer cleanup, fast-transfer removal, and replay protection around fee-claim proofs.
- Invariant to test: one outbound Solana transfer must consume exactly the asset and fee implied by the emitted message and must not let native-vault and bridged-burn branches diverge
- Expected Immunefi impact: Balance manipulation
- Fast validation: Claim once, then replay or mutate non-economic proof fields and assert that no second claim succeeds or blocks unrelated transfers.
