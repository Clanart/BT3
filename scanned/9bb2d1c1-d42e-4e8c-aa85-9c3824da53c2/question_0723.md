# Q723: NEAR Wormhole FinTransfer conversion final settlement and later fee claim can diverge

## Question
Can an unprivileged attacker drive `public Wormhole fee-claim proof flow` so that `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<FinTransferMessage>` settles principal under one interpretation of amount or transfer id while fee claim later uses another because of Borsh-decodes a Wormhole payload into `FinTransferMessage` and derives the emitter address from the token chain and VAA emitter bytes, violating `fee-claim messages must remain bound to the exact destination event, amount, and fee recipient that the destination chain emitted`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<FinTransferMessage>`
- Entrypoint: `public Wormhole fee-claim proof flow`
- Attacker controls: payload bytes inside a validated VAA, fee recipient string, transfer id, token address chain, and amount
- Exploit idea: Target differences between settle-time denormalization and claim-time recomputation of fee, dust, or relayer substitution.
- Invariant to test: fee-claim messages must remain bound to the exact destination event, amount, and fee recipient that the destination chain emitted
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare settled principal, stored transfer record, and fee-claim proof under edge amounts and assert that the three always reconstruct one consistent event.
