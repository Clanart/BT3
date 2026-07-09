# Q550: NEAR EVM prover verify_proof_callback state update before full validation at boundary values

## Question
Can an unprivileged attacker trigger `callback after querying the EVM light client` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-prover/evm-prover/src/lib.rs::verify_proof_callback` violate `the block-hash domain and parsed log bytes must stay cryptographically bound so one proof cannot be reinterpreted as another bridge event` in the `state update before full validation` attack class because compares the returned safe hash to the expected hash and then parses the EVM log into a `ProverResult` becomes fragile at those edges?

## Target
- File/function: `near/omni-prover/evm-prover/src/lib.rs::verify_proof_callback`
- Entrypoint: `callback after querying the EVM light client`
- Attacker controls: expected block hash, returned safe hash, proof kind, and raw log-entry bytes
- Exploit idea: Look for `completed`, `finalised`, or bitmap writes that happen before every branch-specific validation step and external effect. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: the block-hash domain and parsed log bytes must stay cryptographically bound so one proof cannot be reinterpreted as another bridge event
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force later validation or delivery to fail after replay state was consumed and assert that the transfer cannot be stranded or reopened inconsistently. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
