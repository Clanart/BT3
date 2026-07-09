# Q469: EVM zero-address native path recipient or message ambiguity through cross-module drift

## Question
Can an unprivileged attacker use `public `initTransfer` and `finTransfer` when `tokenAddress == address(0)`` with control over ETH amount, fee, native fee, recipient, and msg.value and desynchronize `evm/src/omni-bridge/contracts/OmniBridge.sol zero-address native branch` from the adjacent the next module that consumes the same asset or transfer id that shares the same asset, nonce, proof subject, or mapping specifically in the `recipient or message ambiguity` attack class because treats the zero address as native ETH in both outbound and inbound flows and relies on arithmetic and raw `call` to deliver value, violating `native-asset handling must not let an attacker smuggle ETH through token-only assumptions or force inconsistent accounting between escrowed ETH and emitted bridge events`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol zero-address native branch`
- Entrypoint: `public `initTransfer` and `finTransfer` when `tokenAddress == address(0)``
- Attacker controls: ETH amount, fee, native fee, recipient, and msg.value
- Exploit idea: Exploit non-canonical string, ByteArray, hex, or account-id forms to make one source-side intent resolve to a different destination-side recipient or message. Focus on drift between this module and the adjacent the next module that consumes the same asset or transfer id.
- Invariant to test: native-asset handling must not let an attacker smuggle ETH through token-only assumptions or force inconsistent accounting between escrowed ETH and emitted bridge events
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check source-side serialization against every downstream parser and assert that equivalent-looking inputs cannot resolve to distinct destination accounts or app messages. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridge.sol zero-address native branch` and the adjacent the next module that consumes the same asset or transfer id after every branch.
