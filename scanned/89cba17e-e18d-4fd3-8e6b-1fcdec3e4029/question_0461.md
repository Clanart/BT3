# Q461: NEAR foreign-chain proof factory binding recipient or fee-recipient rebinding through cross-module drift

## Question
Can an unprivileged attacker use `public proof-consuming bridge callbacks` with control over emitter address returned by the prover, chain kind, factory map contents, and proof kind and desynchronize `near/omni-bridge/src/lib.rs::fin_transfer_callback/deploy_token_callback/bind_token_callback/claim_fee_callback` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `recipient or fee-recipient rebinding` attack class because checks that each decoded proof’s emitter address matches the configured factory for that source chain before changing state, violating `factory binding must not be bypassable by payloads whose token-address chain, emitter bytes, or proof-kind conversion disagree on the real source domain`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer_callback/deploy_token_callback/bind_token_callback/claim_fee_callback`
- Entrypoint: `public proof-consuming bridge callbacks`
- Attacker controls: emitter address returned by the prover, chain kind, factory map contents, and proof kind
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: factory binding must not be bypassable by payloads whose token-address chain, emitter bytes, or proof-kind conversion disagree on the real source domain
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::fin_transfer_callback/deploy_token_callback/bind_token_callback/claim_fee_callback` and the adjacent token-mapping and asset-identity logic after every branch.
