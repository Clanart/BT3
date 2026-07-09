# Q1896: Solana init_transfer fee and principal split divergence at boundary values

## Question
Can an unprivileged attacker trigger `public Solana `init_transfer` instruction` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `solana/programs/bridge_token_factory/src/lib.rs::init_transfer` violate `one outbound Solana transfer must consume exactly the asset and fee implied by the emitted message and must not let native-vault and bridged-burn branches diverge` in the `fee and principal split divergence` attack class because charges native fee into the SOL vault, either transfers native custody into the vault or burns bridged supply, and posts a Near-bound message becomes fragile at those edges?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::init_transfer`
- Entrypoint: `public Solana `init_transfer` instruction`
- Attacker controls: mint, source token account, optional vault, user signer, amount, fee, native fee, recipient string, and message
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: one outbound Solana transfer must consume exactly the asset and fee implied by the emitted message and must not let native-vault and bridged-burn branches diverge
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
