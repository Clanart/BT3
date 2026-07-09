# Q2323: NEAR EVM prover verify_proof_callback parser boundary or offset manipulation through cross-module drift

## Question
Can an unprivileged attacker use `callback after querying the EVM light client` with control over expected block hash, returned safe hash, proof kind, and raw log-entry bytes and desynchronize `near/omni-prover/evm-prover/src/lib.rs::verify_proof_callback` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `parser boundary or offset manipulation` attack class because compares the returned safe hash to the expected hash and then parses the EVM log into a `ProverResult`, violating `the block-hash domain and parsed log bytes must stay cryptographically bound so one proof cannot be reinterpreted as another bridge event`?

## Target
- File/function: `near/omni-prover/evm-prover/src/lib.rs::verify_proof_callback`
- Entrypoint: `callback after querying the EVM light client`
- Attacker controls: expected block hash, returned safe hash, proof kind, and raw log-entry bytes
- Exploit idea: Attack offset arithmetic, length prefixes, bucket indices, or body slicing in proof decoders. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: the block-hash domain and parsed log bytes must stay cryptographically bound so one proof cannot be reinterpreted as another bridge event
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Fuzz minimal, maximal, and malformed lengths and assert that every accepted proof round-trips into exactly the intended structured fields. Also assert cross-module consistency between `near/omni-prover/evm-prover/src/lib.rs::verify_proof_callback` and the adjacent proof parsing and source authentication after every branch.
