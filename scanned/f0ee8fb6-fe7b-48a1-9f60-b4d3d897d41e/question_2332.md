# Q2332: NEAR Wormhole byte_utils optional-field encoding ambiguity through cross-module drift

## Question
Can an unprivileged attacker use `get_u8/get_u16/get_u32/get_u64/get_bytes32 via public Wormhole proof parsing` with control over raw VAA bytes and the exact offsets consumed by the parser and desynchronize `near/omni-prover/wormhole-omni-prover-proxy/src/byte_utils.rs::ByteUtils` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `optional-field encoding ambiguity` attack class because provides raw big-endian byte reads that the VAA parser uses to interpret structured fields from untrusted bytes, violating `every field extraction must reject or safely fail on malformed lengths so offset tricks cannot reinterpret one field as another`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/byte_utils.rs::ByteUtils`
- Entrypoint: `get_u8/get_u16/get_u32/get_u64/get_bytes32 via public Wormhole proof parsing`
- Attacker controls: raw VAA bytes and the exact offsets consumed by the parser
- Exploit idea: Focus on empty strings, optional fee recipients, and zero-length messages that are encoded specially. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: every field extraction must reject or safely fail on malformed lengths so offset tricks cannot reinterpret one field as another
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Generate payloads that differ only by optional-field encoding and assert that they cannot verify under the same proof while producing different bridge behavior. Also assert cross-module consistency between `near/omni-prover/wormhole-omni-prover-proxy/src/byte_utils.rs::ByteUtils` and the adjacent proof parsing and source authentication after every branch.
