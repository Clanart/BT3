# Q1433: NEAR omni-types EVM header parser partial EVM validation leaves exploitable gap

## Question
Can an unprivileged attacker provide an EVM proof to `public EVM proof path through `verify_proof`` that passes inclusion checks in `near/omni-types/src/evm/header.rs::BlockHeader` while the decoded receipt or log still authorizes a different bridge action because of decodes block headers that underpin receipt-proof verification, violating `header parsing must not accept malformed or cross-fork data that still drives a valid-looking receipt proof toward bridge settlement`?

## Target
- File/function: `near/omni-types/src/evm/header.rs::BlockHeader`
- Entrypoint: `public EVM proof path through `verify_proof``
- Attacker controls: RLP-encoded header bytes and all decoded header fields including receipts root and hash presence
- Exploit idea: Probe inconsistencies between receipt inclusion, log selection, event decoding, and block-hash validation.
- Invariant to test: header parsing must not accept malformed or cross-fork data that still drives a valid-looking receipt proof toward bridge settlement
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Mutate one proof component at a time and assert that no accepted proof can change any economically-relevant decoded field after inclusion succeeds.
