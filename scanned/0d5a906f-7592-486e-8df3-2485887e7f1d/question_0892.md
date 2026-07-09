# Q892: NEAR Wormhole byte_utils parser boundary or offset manipulation via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `get_u8/get_u16/get_u32/get_u64/get_bytes32 via public Wormhole proof parsing` and then replay or reorder another proof-consuming public entrypoint so that `near/omni-prover/wormhole-omni-prover-proxy/src/byte_utils.rs::ByteUtils` ends up accepting two inconsistent interpretations of the same economic event specifically around `parser boundary or offset manipulation` under provides raw big-endian byte reads that the VAA parser uses to interpret structured fields from untrusted bytes, violating `every field extraction must reject or safely fail on malformed lengths so offset tricks cannot reinterpret one field as another`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/byte_utils.rs::ByteUtils`
- Entrypoint: `get_u8/get_u16/get_u32/get_u64/get_bytes32 via public Wormhole proof parsing`
- Attacker controls: raw VAA bytes and the exact offsets consumed by the parser
- Exploit idea: Attack offset arithmetic, length prefixes, bucket indices, or body slicing in proof decoders. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: every field extraction must reject or safely fail on malformed lengths so offset tricks cannot reinterpret one field as another
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Fuzz minimal, maximal, and malformed lengths and assert that every accepted proof round-trips into exactly the intended structured fields. Then replay or reorder another proof-consuming public entrypoint and assert that the bridge still exposes only one valid economic outcome.
