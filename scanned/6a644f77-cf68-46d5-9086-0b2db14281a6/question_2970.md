# Q2970: NEAR omni-types EVM header parser address normalization changes authenticated subject through cross-module drift

## Question
Can an unprivileged attacker use `public EVM proof path through `verify_proof`` with control over RLP-encoded header bytes and all decoded header fields including receipts root and hash presence and desynchronize `near/omni-types/src/evm/header.rs::BlockHeader` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `address normalization changes authenticated subject` attack class because decodes block headers that underpin receipt-proof verification, violating `header parsing must not accept malformed or cross-fork data that still drives a valid-looking receipt proof toward bridge settlement`?

## Target
- File/function: `near/omni-types/src/evm/header.rs::BlockHeader`
- Entrypoint: `public EVM proof path through `verify_proof``
- Attacker controls: RLP-encoded header bytes and all decoded header fields including receipts root and hash presence
- Exploit idea: Target hex, byte-array, and account-id conversions between proof parsing and token/recipient lookup. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: header parsing must not accept malformed or cross-fork data that still drives a valid-looking receipt proof toward bridge settlement
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip every proof-derived address through all local conversions and assert that normalization never changes the bridge subject. Also assert cross-module consistency between `near/omni-types/src/evm/header.rs::BlockHeader` and the adjacent proof parsing and source authentication after every branch.
