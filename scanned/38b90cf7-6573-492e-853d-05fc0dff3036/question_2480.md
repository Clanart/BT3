# Q2480: NEAR Wormhole InitTransfer conversion parser boundary or offset manipulation at boundary values

## Question
Can an unprivileged attacker trigger `public Wormhole init-transfer proof flow` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<InitTransferMessage>` violate `decoded fields and derived source identity must stay bound to the intended source chain so a payload cannot be replayed across chain/address domains` in the `parser boundary or offset manipulation` attack class because Borsh-decodes a Wormhole payload into `InitTransferMessage` and derives the emitter address using `token_address.get_chain()` plus the VAA emitter bytes becomes fragile at those edges?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<InitTransferMessage>`
- Entrypoint: `public Wormhole init-transfer proof flow`
- Attacker controls: payload bytes inside a validated VAA, recipient string, token address chain, sender address, fee fields, and message
- Exploit idea: Attack offset arithmetic, length prefixes, bucket indices, or body slicing in proof decoders. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: decoded fields and derived source identity must stay bound to the intended source chain so a payload cannot be replayed across chain/address domains
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Fuzz minimal, maximal, and malformed lengths and assert that every accepted proof round-trips into exactly the intended structured fields. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
