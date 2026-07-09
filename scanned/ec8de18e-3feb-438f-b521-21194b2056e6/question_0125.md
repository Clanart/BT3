# Q125: NEAR foreign-chain proof factory binding recipient or fee-recipient rebinding

## Question
Can an unprivileged attacker submit data through `public proof-consuming bridge callbacks` that makes `near/omni-bridge/src/lib.rs::fin_transfer_callback/deploy_token_callback/bind_token_callback/claim_fee_callback` settle principal to one party but authorize fee claim or callback routing for another due to checks that each decoded proof’s emitter address matches the configured factory for that source chain before changing state, violating `factory binding must not be bypassable by payloads whose token-address chain, emitter bytes, or proof-kind conversion disagree on the real source domain`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer_callback/deploy_token_callback/bind_token_callback/claim_fee_callback`
- Entrypoint: `public proof-consuming bridge callbacks`
- Attacker controls: emitter address returned by the prover, chain kind, factory map contents, and proof kind
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities.
- Invariant to test: factory binding must not be bypassable by payloads whose token-address chain, emitter bytes, or proof-kind conversion disagree on the real source domain
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple.
