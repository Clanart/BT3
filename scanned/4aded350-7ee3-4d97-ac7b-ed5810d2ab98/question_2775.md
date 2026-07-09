# Q2775: NEAR Wormhole prover verify_vaa_callback emitter or factory binding mismatch via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `callback after external Wormhole verification` and then replay or reorder later bind, deploy, or metadata-consumption step so that `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_vaa_callback` ends up accepting two inconsistent interpretations of the same economic event specifically around `emitter or factory binding mismatch` under decodes hex, parses the VAA structure, checks that the first payload byte matches the expected proof kind, and converts it into a typed `ProverResult`, violating `callback parsing must never let an attacker swap message class, emitter identity, or payload fields after external validity has already been accepted`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_vaa_callback`
- Entrypoint: `callback after external Wormhole verification`
- Attacker controls: proof kind, VAA bytes, callback success/failure, and local payload parsing
- Exploit idea: Target derivation of emitter identity from token-address chain, VAA bytes, or factory maps. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: callback parsing must never let an attacker swap message class, emitter identity, or payload fields after external validity has already been accepted
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Forge mismatched token-chain and emitter-chain combinations and assert that source authentication fails unless every binding agrees. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
