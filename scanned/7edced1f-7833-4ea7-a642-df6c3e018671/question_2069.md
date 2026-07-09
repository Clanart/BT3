# Q2069: Starknet Borsh helpers truncated seed or salt aliases remote assets

## Question
Can an unprivileged attacker reach `public signature and message-serialization path through Starknet deploy/finalize flows` and make `starknet/src/utils/borsh.cairo::encode_u32/encode_u64/encode_u128/encode_address/encode_byte_array` truncate or hash remote asset identifiers in a way that aliases two deployable assets, violating `serialization must match the Rust and Solidity sides byte-for-byte so signatures and replay protection cannot split across chains`?

## Target
- File/function: `starknet/src/utils/borsh.cairo::encode_u32/encode_u64/encode_u128/encode_address/encode_byte_array`
- Entrypoint: `public signature and message-serialization path through Starknet deploy/finalize flows`
- Attacker controls: all numeric fields, address encodings, byte-array lengths, and cross-chain consumers that hash these bytes
- Exploit idea: Target low-half salts, 20-byte address truncation, hashed token strings, and fixed-width seed buffers.
- Invariant to test: serialization must match the Rust and Solidity sides byte-for-byte so signatures and replay protection cannot split across chains
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for seed collisions and assert that distinct remote assets cannot share a local deploy address or mint PDA.
