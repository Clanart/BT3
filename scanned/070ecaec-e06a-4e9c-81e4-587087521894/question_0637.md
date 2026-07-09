# Q637: EVM zero-address native path recipient or message ambiguity at boundary values

## Question
Can an unprivileged attacker trigger `public `initTransfer` and `finTransfer` when `tokenAddress == address(0)`` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `evm/src/omni-bridge/contracts/OmniBridge.sol zero-address native branch` violate `native-asset handling must not let an attacker smuggle ETH through token-only assumptions or force inconsistent accounting between escrowed ETH and emitted bridge events` in the `recipient or message ambiguity` attack class because treats the zero address as native ETH in both outbound and inbound flows and relies on arithmetic and raw `call` to deliver value becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol zero-address native branch`
- Entrypoint: `public `initTransfer` and `finTransfer` when `tokenAddress == address(0)``
- Attacker controls: ETH amount, fee, native fee, recipient, and msg.value
- Exploit idea: Exploit non-canonical string, ByteArray, hex, or account-id forms to make one source-side intent resolve to a different destination-side recipient or message. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: native-asset handling must not let an attacker smuggle ETH through token-only assumptions or force inconsistent accounting between escrowed ETH and emitted bridge events
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check source-side serialization against every downstream parser and assert that equivalent-looking inputs cannot resolve to distinct destination accounts or app messages. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
