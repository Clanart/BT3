# Q3065: NEAR EVM prover verify_proof shared proof response reused across entrypoints at boundary values

## Question
Can an unprivileged attacker trigger `public EVM proof verifier entrypoint` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-prover/evm-prover/src/lib.rs::verify_proof` violate `the verifier must accept only receipts from the canonical safe chain and only for the exact log entry encoded in the proof` in the `shared proof response reused across entrypoints` attack class because decodes RLP header, receipt, and log entry, checks the log against the receipt, verifies inclusion in the receipts trie, and then asks the light client for the safe block hash becomes fragile at those edges?

## Target
- File/function: `near/omni-prover/evm-prover/src/lib.rs::verify_proof`
- Entrypoint: `public EVM proof verifier entrypoint`
- Attacker controls: serialized `EvmVerifyProofArgs`, log index, receipt index, header, receipt, and trie proof nodes
- Exploit idea: Attack systems where one verifier contract serves deploy, finalize, metadata, and fee-claim flows with a shared result type. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: the verifier must accept only receipts from the canonical safe chain and only for the exact log entry encoded in the proof
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Attempt to route accepted verifier outputs into every public proof consumer and assert that each entrypoint only accepts its intended result variant and source semantics. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
