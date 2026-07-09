# Q771: EVM Borsh helpers hashed or padded seed collision

## Question
Can an unprivileged attacker reach `public signature and message-serialization path through EVM deploy/init/finalize flows` with overlong or adversarial token identifiers and make `evm/src/common/Borsh.sol::encodeUint32/encodeUint64/encodeUint128/encodeString/encodeBytes/encodeAddress` derive the same local seed or salt for two remote assets because of implements the Solidity side of the bridge’s Borsh-compatible encoding for signed messages and metadata, violating `serialization must stay byte-identical with Near and Starknet expectations so signatures, proof parsing, and replay checks do not fork by implementation`?

## Target
- File/function: `evm/src/common/Borsh.sol::encodeUint32/encodeUint64/encodeUint128/encodeString/encodeBytes/encodeAddress`
- Entrypoint: `public signature and message-serialization path through EVM deploy/init/finalize flows`
- Attacker controls: all numeric fields, strings, bytes, and addresses that become signed or Wormhole-published payloads
- Exploit idea: Target hashed token strings, low-half salts, and deterministic-address truncation.
- Invariant to test: serialization must stay byte-identical with Near and Starknet expectations so signatures, proof parsing, and replay checks do not fork by implementation
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search the seed space for collisions and assert that every derivation function preserves uniqueness of remote asset identity.
