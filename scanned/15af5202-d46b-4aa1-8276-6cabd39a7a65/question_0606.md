# Q606: NEAR omni-types EVM header parser proof kind or event class confusion at boundary values

## Question
Can an unprivileged attacker trigger `public EVM proof path through `verify_proof`` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-types/src/evm/header.rs::BlockHeader` violate `header parsing must not accept malformed or cross-fork data that still drives a valid-looking receipt proof toward bridge settlement` in the `proof kind or event class confusion` attack class because decodes block headers that underpin receipt-proof verification becomes fragile at those edges?

## Target
- File/function: `near/omni-types/src/evm/header.rs::BlockHeader`
- Entrypoint: `public EVM proof path through `verify_proof``
- Attacker controls: RLP-encoded header bytes and all decoded header fields including receipts root and hash presence
- Exploit idea: Look for outer proof envelopes that carry a kind tag separately from the payload bytes they validate. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: header parsing must not accept malformed or cross-fork data that still drives a valid-looking receipt proof toward bridge settlement
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check every proof kind against every parser and assert that no valid envelope can downcast into the wrong bridge action. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
