# Q3117: NEAR omni-types EVM header parser address normalization changes authenticated subject at boundary values

## Question
Can an unprivileged attacker trigger `public EVM proof path through `verify_proof`` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-types/src/evm/header.rs::BlockHeader` violate `header parsing must not accept malformed or cross-fork data that still drives a valid-looking receipt proof toward bridge settlement` in the `address normalization changes authenticated subject` attack class because decodes block headers that underpin receipt-proof verification becomes fragile at those edges?

## Target
- File/function: `near/omni-types/src/evm/header.rs::BlockHeader`
- Entrypoint: `public EVM proof path through `verify_proof``
- Attacker controls: RLP-encoded header bytes and all decoded header fields including receipts root and hash presence
- Exploit idea: Target hex, byte-array, and account-id conversions between proof parsing and token/recipient lookup. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: header parsing must not accept malformed or cross-fork data that still drives a valid-looking receipt proof toward bridge settlement
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip every proof-derived address through all local conversions and assert that normalization never changes the bridge subject. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
