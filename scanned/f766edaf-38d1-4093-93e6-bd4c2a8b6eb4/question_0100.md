# Q100: EVM Borsh helpers state update before full validation

## Question
Can an unprivileged attacker exploit `public signature and message-serialization path through EVM deploy/init/finalize flows` so that `evm/src/common/Borsh.sol::encodeUint32/encodeUint64/encodeUint128/encodeString/encodeBytes/encodeAddress` mutates finalization state before all signature or proof checks implied by implements the Solidity side of the bridge’s Borsh-compatible encoding for signed messages and metadata are complete, violating `serialization must stay byte-identical with Near and Starknet expectations so signatures, proof parsing, and replay checks do not fork by implementation`?

## Target
- File/function: `evm/src/common/Borsh.sol::encodeUint32/encodeUint64/encodeUint128/encodeString/encodeBytes/encodeAddress`
- Entrypoint: `public signature and message-serialization path through EVM deploy/init/finalize flows`
- Attacker controls: all numeric fields, strings, bytes, and addresses that become signed or Wormhole-published payloads
- Exploit idea: Look for `completed`, `finalised`, or bitmap writes that happen before every branch-specific validation step and external effect.
- Invariant to test: serialization must stay byte-identical with Near and Starknet expectations so signatures, proof parsing, and replay checks do not fork by implementation
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force later validation or delivery to fail after replay state was consumed and assert that the transfer cannot be stranded or reopened inconsistently.
