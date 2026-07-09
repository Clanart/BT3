# Q1574: Solana init_transfer fee and principal split divergence via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public Solana `init_transfer` instruction` and then replay or reorder later callback or refund resolution so that `solana/programs/bridge_token_factory/src/lib.rs::init_transfer` ends up accepting two inconsistent interpretations of the same economic event specifically around `fee and principal split divergence` under charges native fee into the SOL vault, either transfers native custody into the vault or burns bridged supply, and posts a Near-bound message, violating `one outbound Solana transfer must consume exactly the asset and fee implied by the emitted message and must not let native-vault and bridged-burn branches diverge`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::init_transfer`
- Entrypoint: `public Solana `init_transfer` instruction`
- Attacker controls: mint, source token account, optional vault, user signer, amount, fee, native fee, recipient string, and message
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: one outbound Solana transfer must consume exactly the asset and fee implied by the emitted message and must not let native-vault and bridged-burn branches diverge
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
