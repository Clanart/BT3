# Q1381: NEAR Wormhole prover verify_vaa_callback missing chain or contract domain separation

## Question
Can an unprivileged attacker reuse a valid proof or signature from one chain, contract, or message domain in `callback after external Wormhole verification` because `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_vaa_callback` relies on decodes hex, parses the VAA structure, checks that the first payload byte matches the expected proof kind, and converts it into a typed `ProverResult` more narrowly than the true trust domain, violating `callback parsing must never let an attacker swap message class, emitter identity, or payload fields after external validity has already been accepted`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_vaa_callback`
- Entrypoint: `callback after external Wormhole verification`
- Attacker controls: proof kind, VAA bytes, callback success/failure, and local payload parsing
- Exploit idea: Target validators keyed by derived signer, block hash, emitter address, or payload bytes that omit some domain field.
- Invariant to test: callback parsing must never let an attacker swap message class, emitter identity, or payload fields after external validity has already been accepted
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Attempt cross-chain and cross-contract replay of the same validated bytes and assert that every trust domain field participates in acceptance.
