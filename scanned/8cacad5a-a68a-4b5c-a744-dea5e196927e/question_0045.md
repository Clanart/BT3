# Q45: NEAR EVM prover verify_proof proof kind or event class confusion

## Question
Can an unprivileged attacker submit bytes through `public EVM proof verifier entrypoint` that `near/omni-prover/evm-prover/src/lib.rs::verify_proof` validates as one proof or event class but later interprets as another because of decodes RLP header, receipt, and log entry, checks the log against the receipt, verifies inclusion in the receipts trie, and then asks the light client for the safe block hash, violating `the verifier must accept only receipts from the canonical safe chain and only for the exact log entry encoded in the proof`?

## Target
- File/function: `near/omni-prover/evm-prover/src/lib.rs::verify_proof`
- Entrypoint: `public EVM proof verifier entrypoint`
- Attacker controls: serialized `EvmVerifyProofArgs`, log index, receipt index, header, receipt, and trie proof nodes
- Exploit idea: Look for outer proof envelopes that carry a kind tag separately from the payload bytes they validate.
- Invariant to test: the verifier must accept only receipts from the canonical safe chain and only for the exact log entry encoded in the proof
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check every proof kind against every parser and assert that no valid envelope can downcast into the wrong bridge action.
