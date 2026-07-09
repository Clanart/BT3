# Q2376: NEAR omni-types EVM header parser optional-field encoding ambiguity through cross-module drift

## Question
Can an unprivileged attacker use `public EVM proof path through `verify_proof`` with control over RLP-encoded header bytes and all decoded header fields including receipts root and hash presence and desynchronize `near/omni-types/src/evm/header.rs::BlockHeader` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `optional-field encoding ambiguity` attack class because decodes block headers that underpin receipt-proof verification, violating `header parsing must not accept malformed or cross-fork data that still drives a valid-looking receipt proof toward bridge settlement`?

## Target
- File/function: `near/omni-types/src/evm/header.rs::BlockHeader`
- Entrypoint: `public EVM proof path through `verify_proof``
- Attacker controls: RLP-encoded header bytes and all decoded header fields including receipts root and hash presence
- Exploit idea: Focus on empty strings, optional fee recipients, and zero-length messages that are encoded specially. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: header parsing must not accept malformed or cross-fork data that still drives a valid-looking receipt proof toward bridge settlement
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Generate payloads that differ only by optional-field encoding and assert that they cannot verify under the same proof while producing different bridge behavior. Also assert cross-module consistency between `near/omni-types/src/evm/header.rs::BlockHeader` and the adjacent proof parsing and source authentication after every branch.
