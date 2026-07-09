# Q2478: NEAR Wormhole prover verify_vaa_callback parser boundary or offset manipulation at boundary values

## Question
Can an unprivileged attacker trigger `callback after external Wormhole verification` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_vaa_callback` violate `callback parsing must never let an attacker swap message class, emitter identity, or payload fields after external validity has already been accepted` in the `parser boundary or offset manipulation` attack class because decodes hex, parses the VAA structure, checks that the first payload byte matches the expected proof kind, and converts it into a typed `ProverResult` becomes fragile at those edges?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_vaa_callback`
- Entrypoint: `callback after external Wormhole verification`
- Attacker controls: proof kind, VAA bytes, callback success/failure, and local payload parsing
- Exploit idea: Attack offset arithmetic, length prefixes, bucket indices, or body slicing in proof decoders. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: callback parsing must never let an attacker swap message class, emitter identity, or payload fields after external validity has already been accepted
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Fuzz minimal, maximal, and malformed lengths and assert that every accepted proof round-trips into exactly the intended structured fields. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
