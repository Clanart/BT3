# Q3211: NEAR EVM prover verify_proof address normalization changes authenticated subject

## Question
Can an unprivileged attacker craft proof bytes for `public EVM proof verifier entrypoint` such that `near/omni-prover/evm-prover/src/lib.rs::verify_proof` authenticates an address in one representation but later maps a normalized form to a different asset or account because of decodes RLP header, receipt, and log entry, checks the log against the receipt, verifies inclusion in the receipts trie, and then asks the light client for the safe block hash, violating `the verifier must accept only receipts from the canonical safe chain and only for the exact log entry encoded in the proof`?

## Target
- File/function: `near/omni-prover/evm-prover/src/lib.rs::verify_proof`
- Entrypoint: `public EVM proof verifier entrypoint`
- Attacker controls: serialized `EvmVerifyProofArgs`, log index, receipt index, header, receipt, and trie proof nodes
- Exploit idea: Target hex, byte-array, and account-id conversions between proof parsing and token/recipient lookup.
- Invariant to test: the verifier must accept only receipts from the canonical safe chain and only for the exact log entry encoded in the proof
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip every proof-derived address through all local conversions and assert that normalization never changes the bridge subject.
