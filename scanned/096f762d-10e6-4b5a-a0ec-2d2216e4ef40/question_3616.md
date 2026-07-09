# Q3616: NEAR EVM prover verify_proof address normalization changes authenticated subject at boundary values

## Question
Can an unprivileged attacker trigger `public EVM proof verifier entrypoint` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-prover/evm-prover/src/lib.rs::verify_proof` violate `the verifier must accept only receipts from the canonical safe chain and only for the exact log entry encoded in the proof` in the `address normalization changes authenticated subject` attack class because decodes RLP header, receipt, and log entry, checks the log against the receipt, verifies inclusion in the receipts trie, and then asks the light client for the safe block hash becomes fragile at those edges?

## Target
- File/function: `near/omni-prover/evm-prover/src/lib.rs::verify_proof`
- Entrypoint: `public EVM proof verifier entrypoint`
- Attacker controls: serialized `EvmVerifyProofArgs`, log index, receipt index, header, receipt, and trie proof nodes
- Exploit idea: Target hex, byte-array, and account-id conversions between proof parsing and token/recipient lookup. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: the verifier must accept only receipts from the canonical safe chain and only for the exact log entry encoded in the proof
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip every proof-derived address through all local conversions and assert that normalization never changes the bridge subject. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
