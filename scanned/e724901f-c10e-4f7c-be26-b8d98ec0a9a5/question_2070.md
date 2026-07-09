# Q2070: EVM Borsh helpers truncated seed or salt aliases remote assets

## Question
Can an unprivileged attacker reach `public signature and message-serialization path through EVM deploy/init/finalize flows` and make `evm/src/common/Borsh.sol::encodeUint32/encodeUint64/encodeUint128/encodeString/encodeBytes/encodeAddress` truncate or hash remote asset identifiers in a way that aliases two deployable assets, violating `serialization must stay byte-identical with Near and Starknet expectations so signatures, proof parsing, and replay checks do not fork by implementation`?

## Target
- File/function: `evm/src/common/Borsh.sol::encodeUint32/encodeUint64/encodeUint128/encodeString/encodeBytes/encodeAddress`
- Entrypoint: `public signature and message-serialization path through EVM deploy/init/finalize flows`
- Attacker controls: all numeric fields, strings, bytes, and addresses that become signed or Wormhole-published payloads
- Exploit idea: Target low-half salts, 20-byte address truncation, hashed token strings, and fixed-width seed buffers.
- Invariant to test: serialization must stay byte-identical with Near and Starknet expectations so signatures, proof parsing, and replay checks do not fork by implementation
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for seed collisions and assert that distinct remote assets cannot share a local deploy address or mint PDA.
