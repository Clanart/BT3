# Q3487: NEAR Wormhole InitTransfer conversion optional-field encoding ambiguity through cross-module drift

## Question
Can an unprivileged attacker use `public Wormhole init-transfer proof flow` with control over payload bytes inside a validated VAA, recipient string, token address chain, sender address, fee fields, and message and desynchronize `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<InitTransferMessage>` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `optional-field encoding ambiguity` attack class because Borsh-decodes a Wormhole payload into `InitTransferMessage` and derives the emitter address using `token_address.get_chain()` plus the VAA emitter bytes, violating `decoded fields and derived source identity must stay bound to the intended source chain so a payload cannot be replayed across chain/address domains`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<InitTransferMessage>`
- Entrypoint: `public Wormhole init-transfer proof flow`
- Attacker controls: payload bytes inside a validated VAA, recipient string, token address chain, sender address, fee fields, and message
- Exploit idea: Focus on empty strings, optional fee recipients, and zero-length messages that are encoded specially. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: decoded fields and derived source identity must stay bound to the intended source chain so a payload cannot be replayed across chain/address domains
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Generate payloads that differ only by optional-field encoding and assert that they cannot verify under the same proof while producing different bridge behavior. Also assert cross-module consistency between `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<InitTransferMessage>` and the adjacent proof parsing and source authentication after every branch.
