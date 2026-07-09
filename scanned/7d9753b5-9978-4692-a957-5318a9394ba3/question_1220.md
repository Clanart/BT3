# Q1220: NEAR Wormhole InitTransfer conversion final settlement and later fee claim can diverge at boundary values

## Question
Can an unprivileged attacker trigger `public Wormhole init-transfer proof flow` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<InitTransferMessage>` violate `decoded fields and derived source identity must stay bound to the intended source chain so a payload cannot be replayed across chain/address domains` in the `final settlement and later fee claim can diverge` attack class because Borsh-decodes a Wormhole payload into `InitTransferMessage` and derives the emitter address using `token_address.get_chain()` plus the VAA emitter bytes becomes fragile at those edges?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<InitTransferMessage>`
- Entrypoint: `public Wormhole init-transfer proof flow`
- Attacker controls: payload bytes inside a validated VAA, recipient string, token address chain, sender address, fee fields, and message
- Exploit idea: Target differences between settle-time denormalization and claim-time recomputation of fee, dust, or relayer substitution. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: decoded fields and derived source identity must stay bound to the intended source chain so a payload cannot be replayed across chain/address domains
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare settled principal, stored transfer record, and fee-claim proof under edge amounts and assert that the three always reconstruct one consistent event. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
