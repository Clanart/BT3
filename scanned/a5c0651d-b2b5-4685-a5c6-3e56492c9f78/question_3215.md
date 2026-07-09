# Q3215: NEAR Wormhole prover verify_vaa_callback signature malleability or alternate recovery

## Question
Can an unprivileged attacker submit alternate signature encodings through `callback after external Wormhole verification` that `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_vaa_callback` treats as authorizing the same or a different bridge action because of decodes hex, parses the VAA structure, checks that the first payload byte matches the expected proof kind, and converts it into a typed `ProverResult`, violating `callback parsing must never let an attacker swap message class, emitter identity, or payload fields after external validity has already been accepted`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs::verify_vaa_callback`
- Entrypoint: `callback after external Wormhole verification`
- Attacker controls: proof kind, VAA bytes, callback success/failure, and local payload parsing
- Exploit idea: Target `v/r/s` normalization, ECDSA recovery semantics, and Ethereum-style signature handling on non-Ethereum chains.
- Invariant to test: callback parsing must never let an attacker swap message class, emitter identity, or payload fields after external validity has already been accepted
- Expected Immunefi impact: Cryptographic flaws
- Fast validation: Try low-s/high-s and alternate-`v` forms and assert that recovery either rejects them or yields one unique signer and one unique message.
