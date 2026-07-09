# Q3938: NEAR foreign-chain proof factory binding address normalization changes authenticated subject via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public proof-consuming bridge callbacks` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/lib.rs::fin_transfer_callback/deploy_token_callback/bind_token_callback/claim_fee_callback` ends up accepting two inconsistent interpretations of the same economic event specifically around `address normalization changes authenticated subject` under checks that each decoded proof’s emitter address matches the configured factory for that source chain before changing state, violating `factory binding must not be bypassable by payloads whose token-address chain, emitter bytes, or proof-kind conversion disagree on the real source domain`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer_callback/deploy_token_callback/bind_token_callback/claim_fee_callback`
- Entrypoint: `public proof-consuming bridge callbacks`
- Attacker controls: emitter address returned by the prover, chain kind, factory map contents, and proof kind
- Exploit idea: Target hex, byte-array, and account-id conversions between proof parsing and token/recipient lookup. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: factory binding must not be bypassable by payloads whose token-address chain, emitter bytes, or proof-kind conversion disagree on the real source domain
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip every proof-derived address through all local conversions and assert that normalization never changes the bridge subject. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
