# Q1580: Solana InitTransfer::process fee and principal split divergence via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public outbound flow through `init_transfer`` and then replay or reorder the later settlement leg on another chain so that `solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs::process` ends up accepting two inconsistent interpretations of the same economic event specifically around `fee and principal split divergence` under routes an outbound transfer through native-vault custody or bridged-burn semantics and posts the serialized message to Wormhole/Near, violating `the posted payload must remain exactly backed by the consumed token or SOL value regardless of which branch executes`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs::process`
- Entrypoint: `public outbound flow through `init_transfer``
- Attacker controls: mint/vault branch choice, user signer, token account contents, amount, fee, native fee, recipient string, and message
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: the posted payload must remain exactly backed by the consumed token or SOL value regardless of which branch executes
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value. Then replay or reorder the later settlement leg on another chain and assert that the bridge still exposes only one valid economic outcome.
