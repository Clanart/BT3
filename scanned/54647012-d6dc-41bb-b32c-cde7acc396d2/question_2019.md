# Q2019: NEAR EVM prover verify_proof_callback parser boundary or offset manipulation

## Question
Can an unprivileged attacker craft proof bytes for `callback after querying the EVM light client` that make `near/omni-prover/evm-prover/src/lib.rs::verify_proof_callback` shift field boundaries, truncate payloads, or reinterpret trailing bytes because of compares the returned safe hash to the expected hash and then parses the EVM log into a `ProverResult`, violating `the block-hash domain and parsed log bytes must stay cryptographically bound so one proof cannot be reinterpreted as another bridge event`?

## Target
- File/function: `near/omni-prover/evm-prover/src/lib.rs::verify_proof_callback`
- Entrypoint: `callback after querying the EVM light client`
- Attacker controls: expected block hash, returned safe hash, proof kind, and raw log-entry bytes
- Exploit idea: Attack offset arithmetic, length prefixes, bucket indices, or body slicing in proof decoders.
- Invariant to test: the block-hash domain and parsed log bytes must stay cryptographically bound so one proof cannot be reinterpreted as another bridge event
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Fuzz minimal, maximal, and malformed lengths and assert that every accepted proof round-trips into exactly the intended structured fields.
