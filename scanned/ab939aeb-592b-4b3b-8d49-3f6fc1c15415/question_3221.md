# Q3221: NEAR Wormhole byte_utils length or offset shift reinterprets adjacent fields

## Question
Can an unprivileged attacker supply bytes through `get_u8/get_u16/get_u32/get_u64/get_bytes32 via public Wormhole proof parsing` that make `near/omni-prover/wormhole-omni-prover-proxy/src/byte_utils.rs::ByteUtils` shift a parser boundary and reinterpret one field as another because of provides raw big-endian byte reads that the VAA parser uses to interpret structured fields from untrusted bytes, violating `every field extraction must reject or safely fail on malformed lengths so offset tricks cannot reinterpret one field as another`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/byte_utils.rs::ByteUtils`
- Entrypoint: `get_u8/get_u16/get_u32/get_u64/get_bytes32 via public Wormhole proof parsing`
- Attacker controls: raw VAA bytes and the exact offsets consumed by the parser
- Exploit idea: Target VAA body offsets, byte slicing helpers, RLP decoders, and Borsh length prefixes.
- Invariant to test: every field extraction must reject or safely fail on malformed lengths so offset tricks cannot reinterpret one field as another
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Fuzz underlong, overlong, and near-boundary payloads and assert that accepted bytes decode to exactly one structured message.
