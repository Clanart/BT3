# Q3218: NEAR Wormhole FinTransfer conversion optional-field encoding ambiguity

## Question
Can an unprivileged attacker exploit empty-versus-present optional fields in proofs reaching `public Wormhole fee-claim proof flow` so that `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<FinTransferMessage>` authenticates one payload but downstream logic interprets another because of Borsh-decodes a Wormhole payload into `FinTransferMessage` and derives the emitter address from the token chain and VAA emitter bytes, violating `fee-claim messages must remain bound to the exact destination event, amount, and fee recipient that the destination chain emitted`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<FinTransferMessage>`
- Entrypoint: `public Wormhole fee-claim proof flow`
- Attacker controls: payload bytes inside a validated VAA, fee recipient string, transfer id, token address chain, and amount
- Exploit idea: Focus on empty strings, optional fee recipients, and zero-length messages that are encoded specially.
- Invariant to test: fee-claim messages must remain bound to the exact destination event, amount, and fee recipient that the destination chain emitted
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Generate payloads that differ only by optional-field encoding and assert that they cannot verify under the same proof while producing different bridge behavior.
