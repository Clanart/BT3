# Q1464: EVM zero-address native path numeric cast or overflow changes economic meaning

## Question
Can an unprivileged attacker use `public `initTransfer` and `finTransfer` when `tokenAddress == address(0)`` to push `evm/src/omni-bridge/contracts/OmniBridge.sol zero-address native branch` through a cast, overflow, or truncation path that changes amount or nonce semantics, violating `native-asset handling must not let an attacker smuggle ETH through token-only assumptions or force inconsistent accounting between escrowed ETH and emitted bridge events`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol zero-address native branch`
- Entrypoint: `public `initTransfer` and `finTransfer` when `tokenAddress == address(0)``
- Attacker controls: ETH amount, fee, native fee, recipient, and msg.value
- Exploit idea: Focus on `u128`/`u64`/`usize` casts, PDA bucket indices, and amount conversions around maximum values.
- Invariant to test: native-asset handling must not let an attacker smuggle ETH through token-only assumptions or force inconsistent accounting between escrowed ETH and emitted bridge events
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz maximal numbers and assert that every accepted numeric value preserves its economic meaning across all intermediate representations.
