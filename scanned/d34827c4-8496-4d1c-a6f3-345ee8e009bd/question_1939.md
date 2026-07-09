# Q1939: NEAR foreign-chain proof factory binding proof kind or event class confusion at boundary values

## Question
Can an unprivileged attacker trigger `public proof-consuming bridge callbacks` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-bridge/src/lib.rs::fin_transfer_callback/deploy_token_callback/bind_token_callback/claim_fee_callback` violate `factory binding must not be bypassable by payloads whose token-address chain, emitter bytes, or proof-kind conversion disagree on the real source domain` in the `proof kind or event class confusion` attack class because checks that each decoded proof’s emitter address matches the configured factory for that source chain before changing state becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer_callback/deploy_token_callback/bind_token_callback/claim_fee_callback`
- Entrypoint: `public proof-consuming bridge callbacks`
- Attacker controls: emitter address returned by the prover, chain kind, factory map contents, and proof kind
- Exploit idea: Look for outer proof envelopes that carry a kind tag separately from the payload bytes they validate. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: factory binding must not be bypassable by payloads whose token-address chain, emitter bytes, or proof-kind conversion disagree on the real source domain
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check every proof kind against every parser and assert that no valid envelope can downcast into the wrong bridge action. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
