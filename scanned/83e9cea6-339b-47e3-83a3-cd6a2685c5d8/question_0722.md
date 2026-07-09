# Q722: NEAR Wormhole InitTransfer conversion final settlement and later fee claim can diverge

## Question
Can an unprivileged attacker drive `public Wormhole init-transfer proof flow` so that `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<InitTransferMessage>` settles principal under one interpretation of amount or transfer id while fee claim later uses another because of Borsh-decodes a Wormhole payload into `InitTransferMessage` and derives the emitter address using `token_address.get_chain()` plus the VAA emitter bytes, violating `decoded fields and derived source identity must stay bound to the intended source chain so a payload cannot be replayed across chain/address domains`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<InitTransferMessage>`
- Entrypoint: `public Wormhole init-transfer proof flow`
- Attacker controls: payload bytes inside a validated VAA, recipient string, token address chain, sender address, fee fields, and message
- Exploit idea: Target differences between settle-time denormalization and claim-time recomputation of fee, dust, or relayer substitution.
- Invariant to test: decoded fields and derived source identity must stay bound to the intended source chain so a payload cannot be replayed across chain/address domains
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare settled principal, stored transfer record, and fee-claim proof under edge amounts and assert that the three always reconstruct one consistent event.
