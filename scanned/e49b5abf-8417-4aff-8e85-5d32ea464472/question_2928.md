# Q2928: NEAR Wormhole byte_utils address normalization changes authenticated subject through cross-module drift

## Question
Can an unprivileged attacker use `get_u8/get_u16/get_u32/get_u64/get_bytes32 via public Wormhole proof parsing` with control over raw VAA bytes and the exact offsets consumed by the parser and desynchronize `near/omni-prover/wormhole-omni-prover-proxy/src/byte_utils.rs::ByteUtils` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `address normalization changes authenticated subject` attack class because provides raw big-endian byte reads that the VAA parser uses to interpret structured fields from untrusted bytes, violating `every field extraction must reject or safely fail on malformed lengths so offset tricks cannot reinterpret one field as another`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/byte_utils.rs::ByteUtils`
- Entrypoint: `get_u8/get_u16/get_u32/get_u64/get_bytes32 via public Wormhole proof parsing`
- Attacker controls: raw VAA bytes and the exact offsets consumed by the parser
- Exploit idea: Target hex, byte-array, and account-id conversions between proof parsing and token/recipient lookup. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: every field extraction must reject or safely fail on malformed lengths so offset tricks cannot reinterpret one field as another
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip every proof-derived address through all local conversions and assert that normalization never changes the bridge subject. Also assert cross-module consistency between `near/omni-prover/wormhole-omni-prover-proxy/src/byte_utils.rs::ByteUtils` and the adjacent proof parsing and source authentication after every branch.
