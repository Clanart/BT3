# Q603: Starknet Borsh helpers state update before full validation at boundary values

## Question
Can an unprivileged attacker trigger `public signature and message-serialization path through Starknet deploy/finalize flows` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `starknet/src/utils/borsh.cairo::encode_u32/encode_u64/encode_u128/encode_address/encode_byte_array` violate `serialization must match the Rust and Solidity sides byte-for-byte so signatures and replay protection cannot split across chains` in the `state update before full validation` attack class because implements cross-chain Borsh-like encoding for numbers, addresses, and byte arrays that feed signature verification and message interoperability becomes fragile at those edges?

## Target
- File/function: `starknet/src/utils/borsh.cairo::encode_u32/encode_u64/encode_u128/encode_address/encode_byte_array`
- Entrypoint: `public signature and message-serialization path through Starknet deploy/finalize flows`
- Attacker controls: all numeric fields, address encodings, byte-array lengths, and cross-chain consumers that hash these bytes
- Exploit idea: Look for `completed`, `finalised`, or bitmap writes that happen before every branch-specific validation step and external effect. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: serialization must match the Rust and Solidity sides byte-for-byte so signatures and replay protection cannot split across chains
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force later validation or delivery to fail after replay state was consumed and assert that the transfer cannot be stranded or reopened inconsistently. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
