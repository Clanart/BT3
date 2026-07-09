# Q388: NEAR Wormhole FinTransfer conversion recipient or fee-recipient rebinding through cross-module drift

## Question
Can an unprivileged attacker use `public Wormhole fee-claim proof flow` with control over payload bytes inside a validated VAA, fee recipient string, transfer id, token address chain, and amount and desynchronize `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<FinTransferMessage>` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `recipient or fee-recipient rebinding` attack class because Borsh-decodes a Wormhole payload into `FinTransferMessage` and derives the emitter address from the token chain and VAA emitter bytes, violating `fee-claim messages must remain bound to the exact destination event, amount, and fee recipient that the destination chain emitted`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<FinTransferMessage>`
- Entrypoint: `public Wormhole fee-claim proof flow`
- Attacker controls: payload bytes inside a validated VAA, fee recipient string, transfer id, token address chain, and amount
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: fee-claim messages must remain bound to the exact destination event, amount, and fee recipient that the destination chain emitted
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple. Also assert cross-module consistency between `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<FinTransferMessage>` and the adjacent proof parsing and source authentication after every branch.
