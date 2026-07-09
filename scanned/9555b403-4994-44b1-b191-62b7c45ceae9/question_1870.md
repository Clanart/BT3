# Q1870: NEAR Wormhole byte_utils emitter or factory binding mismatch at boundary values

## Question
Can an unprivileged attacker trigger `get_u8/get_u16/get_u32/get_u64/get_bytes32 via public Wormhole proof parsing` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-prover/wormhole-omni-prover-proxy/src/byte_utils.rs::ByteUtils` violate `every field extraction must reject or safely fail on malformed lengths so offset tricks cannot reinterpret one field as another` in the `emitter or factory binding mismatch` attack class because provides raw big-endian byte reads that the VAA parser uses to interpret structured fields from untrusted bytes becomes fragile at those edges?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/byte_utils.rs::ByteUtils`
- Entrypoint: `get_u8/get_u16/get_u32/get_u64/get_bytes32 via public Wormhole proof parsing`
- Attacker controls: raw VAA bytes and the exact offsets consumed by the parser
- Exploit idea: Target derivation of emitter identity from token-address chain, VAA bytes, or factory maps. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: every field extraction must reject or safely fail on malformed lengths so offset tricks cannot reinterpret one field as another
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Forge mismatched token-chain and emitter-chain combinations and assert that source authentication fails unless every binding agrees. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
