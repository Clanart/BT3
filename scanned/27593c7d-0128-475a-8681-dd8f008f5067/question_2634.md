# Q2634: NEAR Wormhole byte_utils address normalization changes authenticated subject

## Question
Can an unprivileged attacker craft proof bytes for `get_u8/get_u16/get_u32/get_u64/get_bytes32 via public Wormhole proof parsing` such that `near/omni-prover/wormhole-omni-prover-proxy/src/byte_utils.rs::ByteUtils` authenticates an address in one representation but later maps a normalized form to a different asset or account because of provides raw big-endian byte reads that the VAA parser uses to interpret structured fields from untrusted bytes, violating `every field extraction must reject or safely fail on malformed lengths so offset tricks cannot reinterpret one field as another`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/byte_utils.rs::ByteUtils`
- Entrypoint: `get_u8/get_u16/get_u32/get_u64/get_bytes32 via public Wormhole proof parsing`
- Attacker controls: raw VAA bytes and the exact offsets consumed by the parser
- Exploit idea: Target hex, byte-array, and account-id conversions between proof parsing and token/recipient lookup.
- Invariant to test: every field extraction must reject or safely fail on malformed lengths so offset tricks cannot reinterpret one field as another
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip every proof-derived address through all local conversions and assert that normalization never changes the bridge subject.
