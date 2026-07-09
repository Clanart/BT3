# Q804: EVM zero-address native path one inbound event spawns multiple outbound obligations

## Question
Can an unprivileged attacker settle through `public `initTransfer` and `finTransfer` when `tokenAddress == address(0)`` and make `evm/src/omni-bridge/contracts/OmniBridge.sol zero-address native branch` both release local value and create a second valid outbound bridge obligation via treats the zero address as native ETH in both outbound and inbound flows and relies on arithmetic and raw `call` to deliver value, violating `native-asset handling must not let an attacker smuggle ETH through token-only assumptions or force inconsistent accounting between escrowed ETH and emitted bridge events`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol zero-address native branch`
- Entrypoint: `public `initTransfer` and `finTransfer` when `tokenAddress == address(0)``
- Attacker controls: ETH amount, fee, native fee, recipient, and msg.value
- Exploit idea: Focus on forward-to-other-chain branches and fast-transfer substitution where an inbound event becomes a new pending transfer.
- Invariant to test: native-asset handling must not let an attacker smuggle ETH through token-only assumptions or force inconsistent accounting between escrowed ETH and emitted bridge events
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Track value and replay state across both the inbound leg and the forwarded leg and assert that one source event cannot increase total outstanding claims.
