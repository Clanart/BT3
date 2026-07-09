# Q3750: NEAR EVM prover verify_proof same remote asset deployable via multiple proof paths

## Question
Can an unprivileged attacker use `public EVM proof verifier entrypoint` to deploy or bind the same remote asset through a second path because `near/omni-prover/evm-prover/src/lib.rs::verify_proof` authenticates decodes RLP header, receipt, and log entry, checks the log against the receipt, verifies inclusion in the receipts trie, and then asks the light client for the safe block hash differently than another deploy path, violating `the verifier must accept only receipts from the canonical safe chain and only for the exact log entry encoded in the proof`?

## Target
- File/function: `near/omni-prover/evm-prover/src/lib.rs::verify_proof`
- Entrypoint: `public EVM proof verifier entrypoint`
- Attacker controls: serialized `EvmVerifyProofArgs`, log index, receipt index, header, receipt, and trie proof nodes
- Exploit idea: Compare metadata-based deployment, deploy-token binding, native-token deployment, and chain-specific extension paths.
- Invariant to test: the verifier must accept only receipts from the canonical safe chain and only for the exact log entry encoded in the proof
- Expected Immunefi impact: Balance manipulation
- Fast validation: Attempt the same remote asset through every supported path and assert that the bridge converges to one canonical local representation.
