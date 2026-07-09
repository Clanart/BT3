# Q2771: NEAR EVM prover verify_proof shared proof response reused across entrypoints via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public EVM proof verifier entrypoint` and then replay or reorder later bind, deploy, or metadata-consumption step so that `near/omni-prover/evm-prover/src/lib.rs::verify_proof` ends up accepting two inconsistent interpretations of the same economic event specifically around `shared proof response reused across entrypoints` under decodes RLP header, receipt, and log entry, checks the log against the receipt, verifies inclusion in the receipts trie, and then asks the light client for the safe block hash, violating `the verifier must accept only receipts from the canonical safe chain and only for the exact log entry encoded in the proof`?

## Target
- File/function: `near/omni-prover/evm-prover/src/lib.rs::verify_proof`
- Entrypoint: `public EVM proof verifier entrypoint`
- Attacker controls: serialized `EvmVerifyProofArgs`, log index, receipt index, header, receipt, and trie proof nodes
- Exploit idea: Attack systems where one verifier contract serves deploy, finalize, metadata, and fee-claim flows with a shared result type. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: the verifier must accept only receipts from the canonical safe chain and only for the exact log entry encoded in the proof
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Attempt to route accepted verifier outputs into every public proof consumer and assert that each entrypoint only accepts its intended result variant and source semantics. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
