# Q46: NEAR EVM prover verify_proof_callback state update before full validation

## Question
Can an unprivileged attacker exploit `callback after querying the EVM light client` so that `near/omni-prover/evm-prover/src/lib.rs::verify_proof_callback` mutates finalization state before all signature or proof checks implied by compares the returned safe hash to the expected hash and then parses the EVM log into a `ProverResult` are complete, violating `the block-hash domain and parsed log bytes must stay cryptographically bound so one proof cannot be reinterpreted as another bridge event`?

## Target
- File/function: `near/omni-prover/evm-prover/src/lib.rs::verify_proof_callback`
- Entrypoint: `callback after querying the EVM light client`
- Attacker controls: expected block hash, returned safe hash, proof kind, and raw log-entry bytes
- Exploit idea: Look for `completed`, `finalised`, or bitmap writes that happen before every branch-specific validation step and external effect.
- Invariant to test: the block-hash domain and parsed log bytes must stay cryptographically bound so one proof cannot be reinterpreted as another bridge event
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force later validation or delivery to fail after replay state was consumed and assert that the transfer cannot be stranded or reopened inconsistently.
