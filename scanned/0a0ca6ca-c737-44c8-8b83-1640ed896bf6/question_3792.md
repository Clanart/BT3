# Q3792: NEAR omni-types EVM header parser one verified event can be reinterpreted as another

## Question
Can an unprivileged attacker feed `public EVM proof path through `verify_proof`` a verified event whose raw bytes `near/omni-types/src/evm/header.rs::BlockHeader` can reinterpret under a second event schema because of decodes block headers that underpin receipt-proof verification, violating `header parsing must not accept malformed or cross-fork data that still drives a valid-looking receipt proof toward bridge settlement`?

## Target
- File/function: `near/omni-types/src/evm/header.rs::BlockHeader`
- Entrypoint: `public EVM proof path through `verify_proof``
- Attacker controls: RLP-encoded header bytes and all decoded header fields including receipts root and hash presence
- Exploit idea: Target shared envelopes and topic/payload parsers for init, finalize, deploy, and metadata events.
- Invariant to test: header parsing must not accept malformed or cross-fork data that still drives a valid-looking receipt proof toward bridge settlement
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Attempt to parse the same verified bytes under every event class and assert that only one parser accepts them.
