# Q1271: NEAR omni-types EVM header parser parser boundary or offset manipulation at boundary values

## Question
Can an unprivileged attacker trigger `public EVM proof path through `verify_proof`` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-types/src/evm/header.rs::BlockHeader` violate `header parsing must not accept malformed or cross-fork data that still drives a valid-looking receipt proof toward bridge settlement` in the `parser boundary or offset manipulation` attack class because decodes block headers that underpin receipt-proof verification becomes fragile at those edges?

## Target
- File/function: `near/omni-types/src/evm/header.rs::BlockHeader`
- Entrypoint: `public EVM proof path through `verify_proof``
- Attacker controls: RLP-encoded header bytes and all decoded header fields including receipts root and hash presence
- Exploit idea: Attack offset arithmetic, length prefixes, bucket indices, or body slicing in proof decoders. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: header parsing must not accept malformed or cross-fork data that still drives a valid-looking receipt proof toward bridge settlement
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Fuzz minimal, maximal, and malformed lengths and assert that every accepted proof round-trips into exactly the intended structured fields. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
