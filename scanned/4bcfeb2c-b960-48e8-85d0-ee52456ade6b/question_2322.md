# Q2322: NEAR EVM prover verify_proof optional-field encoding ambiguity through cross-module drift

## Question
Can an unprivileged attacker use `public EVM proof verifier entrypoint` with control over serialized `EvmVerifyProofArgs`, log index, receipt index, header, receipt, and trie proof nodes and desynchronize `near/omni-prover/evm-prover/src/lib.rs::verify_proof` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `optional-field encoding ambiguity` attack class because decodes RLP header, receipt, and log entry, checks the log against the receipt, verifies inclusion in the receipts trie, and then asks the light client for the safe block hash, violating `the verifier must accept only receipts from the canonical safe chain and only for the exact log entry encoded in the proof`?

## Target
- File/function: `near/omni-prover/evm-prover/src/lib.rs::verify_proof`
- Entrypoint: `public EVM proof verifier entrypoint`
- Attacker controls: serialized `EvmVerifyProofArgs`, log index, receipt index, header, receipt, and trie proof nodes
- Exploit idea: Focus on empty strings, optional fee recipients, and zero-length messages that are encoded specially. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: the verifier must accept only receipts from the canonical safe chain and only for the exact log entry encoded in the proof
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Generate payloads that differ only by optional-field encoding and assert that they cannot verify under the same proof while producing different bridge behavior. Also assert cross-module consistency between `near/omni-prover/evm-prover/src/lib.rs::verify_proof` and the adjacent proof parsing and source authentication after every branch.
