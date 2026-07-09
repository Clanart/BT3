# Q2625: NEAR EVM prover verify_proof_callback partial EVM validation leaves exploitable gap

## Question
Can an unprivileged attacker provide an EVM proof to `callback after querying the EVM light client` that passes inclusion checks in `near/omni-prover/evm-prover/src/lib.rs::verify_proof_callback` while the decoded receipt or log still authorizes a different bridge action because of compares the returned safe hash to the expected hash and then parses the EVM log into a `ProverResult`, violating `the block-hash domain and parsed log bytes must stay cryptographically bound so one proof cannot be reinterpreted as another bridge event`?

## Target
- File/function: `near/omni-prover/evm-prover/src/lib.rs::verify_proof_callback`
- Entrypoint: `callback after querying the EVM light client`
- Attacker controls: expected block hash, returned safe hash, proof kind, and raw log-entry bytes
- Exploit idea: Probe inconsistencies between receipt inclusion, log selection, event decoding, and block-hash validation.
- Invariant to test: the block-hash domain and parsed log bytes must stay cryptographically bound so one proof cannot be reinterpreted as another bridge event
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Mutate one proof component at a time and assert that no accepted proof can change any economically-relevant decoded field after inclusion succeeds.
