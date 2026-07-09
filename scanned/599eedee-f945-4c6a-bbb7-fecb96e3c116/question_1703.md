# Q1703: NEAR Wormhole prover verify_vaa_callback missing chain or contract domain separation through cross-module drift

## Question
Can an unprivileged attacker use `callback after external Wormhole verification` with control over proof kind, VAA bytes, callback success/failure, and local payload parsing and desynchronize `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_vaa_callback` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `missing chain or contract domain separation` attack class because decodes hex, parses the VAA structure, checks that the first payload byte matches the expected proof kind, and converts it into a typed `ProverResult`, violating `callback parsing must never let an attacker swap message class, emitter identity, or payload fields after external validity has already been accepted`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_vaa_callback`
- Entrypoint: `callback after external Wormhole verification`
- Attacker controls: proof kind, VAA bytes, callback success/failure, and local payload parsing
- Exploit idea: Target validators keyed by derived signer, block hash, emitter address, or payload bytes that omit some domain field. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: callback parsing must never let an attacker swap message class, emitter identity, or payload fields after external validity has already been accepted
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Attempt cross-chain and cross-contract replay of the same validated bytes and assert that every trust domain field participates in acceptance. Also assert cross-module consistency between `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_vaa_callback` and the adjacent proof parsing and source authentication after every branch.
