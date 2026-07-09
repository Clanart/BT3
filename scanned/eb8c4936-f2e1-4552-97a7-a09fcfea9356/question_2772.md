# Q2772: NEAR EVM prover verify_proof_callback partial EVM validation leaves exploitable gap via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `callback after querying the EVM light client` and then replay or reorder later bind, deploy, or metadata-consumption step so that `near/omni-prover/evm-prover/src/lib.rs::verify_proof_callback` ends up accepting two inconsistent interpretations of the same economic event specifically around `partial EVM validation leaves exploitable gap` under compares the returned safe hash to the expected hash and then parses the EVM log into a `ProverResult`, violating `the block-hash domain and parsed log bytes must stay cryptographically bound so one proof cannot be reinterpreted as another bridge event`?

## Target
- File/function: `near/omni-prover/evm-prover/src/lib.rs::verify_proof_callback`
- Entrypoint: `callback after querying the EVM light client`
- Attacker controls: expected block hash, returned safe hash, proof kind, and raw log-entry bytes
- Exploit idea: Probe inconsistencies between receipt inclusion, log selection, event decoding, and block-hash validation. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: the block-hash domain and parsed log bytes must stay cryptographically bound so one proof cannot be reinterpreted as another bridge event
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Mutate one proof component at a time and assert that no accepted proof can change any economically-relevant decoded field after inclusion succeeds. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
