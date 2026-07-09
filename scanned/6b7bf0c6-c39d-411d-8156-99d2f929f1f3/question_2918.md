# Q2918: NEAR EVM prover verify_proof shared proof response reused across entrypoints through cross-module drift

## Question
Can an unprivileged attacker use `public EVM proof verifier entrypoint` with control over serialized `EvmVerifyProofArgs`, log index, receipt index, header, receipt, and trie proof nodes and desynchronize `near/omni-prover/evm-prover/src/lib.rs::verify_proof` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `shared proof response reused across entrypoints` attack class because decodes RLP header, receipt, and log entry, checks the log against the receipt, verifies inclusion in the receipts trie, and then asks the light client for the safe block hash, violating `the verifier must accept only receipts from the canonical safe chain and only for the exact log entry encoded in the proof`?

## Target
- File/function: `near/omni-prover/evm-prover/src/lib.rs::verify_proof`
- Entrypoint: `public EVM proof verifier entrypoint`
- Attacker controls: serialized `EvmVerifyProofArgs`, log index, receipt index, header, receipt, and trie proof nodes
- Exploit idea: Attack systems where one verifier contract serves deploy, finalize, metadata, and fee-claim flows with a shared result type. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: the verifier must accept only receipts from the canonical safe chain and only for the exact log entry encoded in the proof
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Attempt to route accepted verifier outputs into every public proof consumer and assert that each entrypoint only accepts its intended result variant and source semantics. Also assert cross-module consistency between `near/omni-prover/evm-prover/src/lib.rs::verify_proof` and the adjacent proof parsing and source authentication after every branch.
