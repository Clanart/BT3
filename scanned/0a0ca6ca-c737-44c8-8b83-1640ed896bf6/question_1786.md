# Q1786: EVM zero-address native path numeric cast or overflow changes economic meaning through cross-module drift

## Question
Can an unprivileged attacker use `public `initTransfer` and `finTransfer` when `tokenAddress == address(0)`` with control over ETH amount, fee, native fee, recipient, and msg.value and desynchronize `evm/src/omni-bridge/contracts/OmniBridge.sol zero-address native branch` from the adjacent the next module that consumes the same asset or transfer id that shares the same asset, nonce, proof subject, or mapping specifically in the `numeric cast or overflow changes economic meaning` attack class because treats the zero address as native ETH in both outbound and inbound flows and relies on arithmetic and raw `call` to deliver value, violating `native-asset handling must not let an attacker smuggle ETH through token-only assumptions or force inconsistent accounting between escrowed ETH and emitted bridge events`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol zero-address native branch`
- Entrypoint: `public `initTransfer` and `finTransfer` when `tokenAddress == address(0)``
- Attacker controls: ETH amount, fee, native fee, recipient, and msg.value
- Exploit idea: Focus on `u128`/`u64`/`usize` casts, PDA bucket indices, and amount conversions around maximum values. Focus on drift between this module and the adjacent the next module that consumes the same asset or transfer id.
- Invariant to test: native-asset handling must not let an attacker smuggle ETH through token-only assumptions or force inconsistent accounting between escrowed ETH and emitted bridge events
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz maximal numbers and assert that every accepted numeric value preserves its economic meaning across all intermediate representations. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridge.sol zero-address native branch` and the adjacent the next module that consumes the same asset or transfer id after every branch.
