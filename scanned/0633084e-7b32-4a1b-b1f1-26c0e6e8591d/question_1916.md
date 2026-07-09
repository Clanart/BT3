# Q1916: NEAR omni-types EVM header parser partial EVM validation leaves exploitable gap at boundary values

## Question
Can an unprivileged attacker trigger `public EVM proof path through `verify_proof`` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-types/src/evm/header.rs::BlockHeader` violate `header parsing must not accept malformed or cross-fork data that still drives a valid-looking receipt proof toward bridge settlement` in the `partial EVM validation leaves exploitable gap` attack class because decodes block headers that underpin receipt-proof verification becomes fragile at those edges?

## Target
- File/function: `near/omni-types/src/evm/header.rs::BlockHeader`
- Entrypoint: `public EVM proof path through `verify_proof``
- Attacker controls: RLP-encoded header bytes and all decoded header fields including receipts root and hash presence
- Exploit idea: Probe inconsistencies between receipt inclusion, log selection, event decoding, and block-hash validation. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: header parsing must not accept malformed or cross-fork data that still drives a valid-looking receipt proof toward bridge settlement
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Mutate one proof component at a time and assert that no accepted proof can change any economically-relevant decoded field after inclusion succeeds. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
