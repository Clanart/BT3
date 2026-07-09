# Q2095: NEAR foreign-chain proof factory binding parser boundary or offset manipulation

## Question
Can an unprivileged attacker craft proof bytes for `public proof-consuming bridge callbacks` that make `near/omni-bridge/src/lib.rs::fin_transfer_callback/deploy_token_callback/bind_token_callback/claim_fee_callback` shift field boundaries, truncate payloads, or reinterpret trailing bytes because of checks that each decoded proof’s emitter address matches the configured factory for that source chain before changing state, violating `factory binding must not be bypassable by payloads whose token-address chain, emitter bytes, or proof-kind conversion disagree on the real source domain`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer_callback/deploy_token_callback/bind_token_callback/claim_fee_callback`
- Entrypoint: `public proof-consuming bridge callbacks`
- Attacker controls: emitter address returned by the prover, chain kind, factory map contents, and proof kind
- Exploit idea: Attack offset arithmetic, length prefixes, bucket indices, or body slicing in proof decoders.
- Invariant to test: factory binding must not be bypassable by payloads whose token-address chain, emitter bytes, or proof-kind conversion disagree on the real source domain
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Fuzz minimal, maximal, and malformed lengths and assert that every accepted proof round-trips into exactly the intended structured fields.
