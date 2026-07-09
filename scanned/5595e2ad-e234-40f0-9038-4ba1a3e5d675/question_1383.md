# Q1383: NEAR Wormhole InitTransfer conversion proof kind or event class confusion

## Question
Can an unprivileged attacker submit bytes through `public Wormhole init-transfer proof flow` that `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<InitTransferMessage>` validates as one proof or event class but later interprets as another because of Borsh-decodes a Wormhole payload into `InitTransferMessage` and derives the emitter address using `token_address.get_chain()` plus the VAA emitter bytes, violating `decoded fields and derived source identity must stay bound to the intended source chain so a payload cannot be replayed across chain/address domains`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<InitTransferMessage>`
- Entrypoint: `public Wormhole init-transfer proof flow`
- Attacker controls: payload bytes inside a validated VAA, recipient string, token address chain, sender address, fee fields, and message
- Exploit idea: Look for outer proof envelopes that carry a kind tag separately from the payload bytes they validate.
- Invariant to test: decoded fields and derived source identity must stay bound to the intended source chain so a payload cannot be replayed across chain/address domains
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check every proof kind against every parser and assert that no valid envelope can downcast into the wrong bridge action.
