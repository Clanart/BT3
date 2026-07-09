# Q2176: NEAR Wormhole InitTransfer conversion parser boundary or offset manipulation via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public Wormhole init-transfer proof flow` and then replay or reorder later fee-claim proof submission so that `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<InitTransferMessage>` ends up accepting two inconsistent interpretations of the same economic event specifically around `parser boundary or offset manipulation` under Borsh-decodes a Wormhole payload into `InitTransferMessage` and derives the emitter address using `token_address.get_chain()` plus the VAA emitter bytes, violating `decoded fields and derived source identity must stay bound to the intended source chain so a payload cannot be replayed across chain/address domains`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<InitTransferMessage>`
- Entrypoint: `public Wormhole init-transfer proof flow`
- Attacker controls: payload bytes inside a validated VAA, recipient string, token address chain, sender address, fee fields, and message
- Exploit idea: Attack offset arithmetic, length prefixes, bucket indices, or body slicing in proof decoders. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: decoded fields and derived source identity must stay bound to the intended source chain so a payload cannot be replayed across chain/address domains
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Fuzz minimal, maximal, and malformed lengths and assert that every accepted proof round-trips into exactly the intended structured fields. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
