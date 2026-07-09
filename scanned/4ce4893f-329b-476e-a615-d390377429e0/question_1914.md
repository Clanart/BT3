# Q1914: EVM Borsh helpers endianness mismatch forks authenticated bytes at boundary values

## Question
Can an unprivileged attacker trigger `public signature and message-serialization path through EVM deploy/init/finalize flows` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `evm/src/common/Borsh.sol::encodeUint32/encodeUint64/encodeUint128/encodeString/encodeBytes/encodeAddress` violate `serialization must stay byte-identical with Near and Starknet expectations so signatures, proof parsing, and replay checks do not fork by implementation` in the `endianness mismatch forks authenticated bytes` attack class because implements the Solidity side of the bridge’s Borsh-compatible encoding for signed messages and metadata becomes fragile at those edges?

## Target
- File/function: `evm/src/common/Borsh.sol::encodeUint32/encodeUint64/encodeUint128/encodeString/encodeBytes/encodeAddress`
- Entrypoint: `public signature and message-serialization path through EVM deploy/init/finalize flows`
- Attacker controls: all numeric fields, strings, bytes, and addresses that become signed or Wormhole-published payloads
- Exploit idea: Target Borsh helpers and hand-built payload encoders across Rust, Solidity, and Cairo. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: serialization must stay byte-identical with Near and Starknet expectations so signatures, proof parsing, and replay checks do not fork by implementation
- Expected Immunefi impact: Cryptographic flaws
- Fast validation: Cross-generate payloads on every implementation and assert byte-for-byte equality for every field combination that can reach signatures or proofs. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
