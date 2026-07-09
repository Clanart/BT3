# Q436: EVM Borsh helpers state update before full validation through cross-module drift

## Question
Can an unprivileged attacker use `public signature and message-serialization path through EVM deploy/init/finalize flows` with control over all numeric fields, strings, bytes, and addresses that become signed or Wormhole-published payloads and desynchronize `evm/src/common/Borsh.sol::encodeUint32/encodeUint64/encodeUint128/encodeString/encodeBytes/encodeAddress` from the adjacent the next module that consumes the same asset or transfer id that shares the same asset, nonce, proof subject, or mapping specifically in the `state update before full validation` attack class because implements the Solidity side of the bridge’s Borsh-compatible encoding for signed messages and metadata, violating `serialization must stay byte-identical with Near and Starknet expectations so signatures, proof parsing, and replay checks do not fork by implementation`?

## Target
- File/function: `evm/src/common/Borsh.sol::encodeUint32/encodeUint64/encodeUint128/encodeString/encodeBytes/encodeAddress`
- Entrypoint: `public signature and message-serialization path through EVM deploy/init/finalize flows`
- Attacker controls: all numeric fields, strings, bytes, and addresses that become signed or Wormhole-published payloads
- Exploit idea: Look for `completed`, `finalised`, or bitmap writes that happen before every branch-specific validation step and external effect. Focus on drift between this module and the adjacent the next module that consumes the same asset or transfer id.
- Invariant to test: serialization must stay byte-identical with Near and Starknet expectations so signatures, proof parsing, and replay checks do not fork by implementation
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force later validation or delivery to fail after replay state was consumed and assert that the transfer cannot be stranded or reopened inconsistently. Also assert cross-module consistency between `evm/src/common/Borsh.sol::encodeUint32/encodeUint64/encodeUint128/encodeString/encodeBytes/encodeAddress` and the adjacent the next module that consumes the same asset or transfer id after every branch.
