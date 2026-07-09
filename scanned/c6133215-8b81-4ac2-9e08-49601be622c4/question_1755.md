# Q1755: NEAR omni-types EVM header parser partial EVM validation leaves exploitable gap through cross-module drift

## Question
Can an unprivileged attacker use `public EVM proof path through `verify_proof`` with control over RLP-encoded header bytes and all decoded header fields including receipts root and hash presence and desynchronize `near/omni-types/src/evm/header.rs::BlockHeader` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `partial EVM validation leaves exploitable gap` attack class because decodes block headers that underpin receipt-proof verification, violating `header parsing must not accept malformed or cross-fork data that still drives a valid-looking receipt proof toward bridge settlement`?

## Target
- File/function: `near/omni-types/src/evm/header.rs::BlockHeader`
- Entrypoint: `public EVM proof path through `verify_proof``
- Attacker controls: RLP-encoded header bytes and all decoded header fields including receipts root and hash presence
- Exploit idea: Probe inconsistencies between receipt inclusion, log selection, event decoding, and block-hash validation. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: header parsing must not accept malformed or cross-fork data that still drives a valid-looking receipt proof toward bridge settlement
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Mutate one proof component at a time and assert that no accepted proof can change any economically-relevant decoded field after inclusion succeeds. Also assert cross-module consistency between `near/omni-types/src/evm/header.rs::BlockHeader` and the adjacent proof parsing and source authentication after every branch.
