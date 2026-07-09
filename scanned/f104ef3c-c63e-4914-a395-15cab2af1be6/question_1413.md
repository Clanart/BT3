# Q1413: Solana init_transfer fee and principal split divergence

## Question
Can an unprivileged attacker enter through `public Solana `init_transfer` instruction` with crafted amount, fee, or native-fee inputs and make `solana/programs/bridge_token_factory/src/lib.rs::init_transfer` use inconsistent fee and principal values across charges native fee into the SOL vault, either transfers native custody into the vault or burns bridged supply, and posts a Near-bound message, violating `one outbound Solana transfer must consume exactly the asset and fee implied by the emitted message and must not let native-vault and bridged-burn branches diverge`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::init_transfer`
- Entrypoint: `public Solana `init_transfer` instruction`
- Attacker controls: mint, source token account, optional vault, user signer, amount, fee, native fee, recipient string, and message
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing.
- Invariant to test: one outbound Solana transfer must consume exactly the asset and fee implied by the emitted message and must not let native-vault and bridged-burn branches diverge
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value.
