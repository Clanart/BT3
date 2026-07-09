# Q2630: NEAR Wormhole InitTransfer conversion emitter or factory binding mismatch

## Question
Can an unprivileged attacker submit a structurally valid proof to `public Wormhole init-transfer proof flow` whose payload points to one source chain while `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<InitTransferMessage>` authenticates another because of Borsh-decodes a Wormhole payload into `InitTransferMessage` and derives the emitter address using `token_address.get_chain()` plus the VAA emitter bytes, violating `decoded fields and derived source identity must stay bound to the intended source chain so a payload cannot be replayed across chain/address domains`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<InitTransferMessage>`
- Entrypoint: `public Wormhole init-transfer proof flow`
- Attacker controls: payload bytes inside a validated VAA, recipient string, token address chain, sender address, fee fields, and message
- Exploit idea: Target derivation of emitter identity from token-address chain, VAA bytes, or factory maps.
- Invariant to test: decoded fields and derived source identity must stay bound to the intended source chain so a payload cannot be replayed across chain/address domains
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Forge mismatched token-chain and emitter-chain combinations and assert that source authentication fails unless every binding agrees.
