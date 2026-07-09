# Q1052: NEAR Wormhole prover verify_vaa_callback proof kind or event class confusion through cross-module drift

## Question
Can an unprivileged attacker use `callback after external Wormhole verification` with control over proof kind, VAA bytes, callback success/failure, and local payload parsing and desynchronize `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_vaa_callback` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `proof kind or event class confusion` attack class because decodes hex, parses the VAA structure, checks that the first payload byte matches the expected proof kind, and converts it into a typed `ProverResult`, violating `callback parsing must never let an attacker swap message class, emitter identity, or payload fields after external validity has already been accepted`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_vaa_callback`
- Entrypoint: `callback after external Wormhole verification`
- Attacker controls: proof kind, VAA bytes, callback success/failure, and local payload parsing
- Exploit idea: Look for outer proof envelopes that carry a kind tag separately from the payload bytes they validate. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: callback parsing must never let an attacker swap message class, emitter identity, or payload fields after external validity has already been accepted
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check every proof kind against every parser and assert that no valid envelope can downcast into the wrong bridge action. Also assert cross-module consistency between `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_vaa_callback` and the adjacent proof parsing and source authentication after every branch.
