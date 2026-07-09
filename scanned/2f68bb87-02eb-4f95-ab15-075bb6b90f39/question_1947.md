# Q1947: EVM zero-address native path numeric cast or overflow changes economic meaning at boundary values

## Question
Can an unprivileged attacker trigger `public `initTransfer` and `finTransfer` when `tokenAddress == address(0)`` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `evm/src/omni-bridge/contracts/OmniBridge.sol zero-address native branch` violate `native-asset handling must not let an attacker smuggle ETH through token-only assumptions or force inconsistent accounting between escrowed ETH and emitted bridge events` in the `numeric cast or overflow changes economic meaning` attack class because treats the zero address as native ETH in both outbound and inbound flows and relies on arithmetic and raw `call` to deliver value becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol zero-address native branch`
- Entrypoint: `public `initTransfer` and `finTransfer` when `tokenAddress == address(0)``
- Attacker controls: ETH amount, fee, native fee, recipient, and msg.value
- Exploit idea: Focus on `u128`/`u64`/`usize` casts, PDA bucket indices, and amount conversions around maximum values. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: native-asset handling must not let an attacker smuggle ETH through token-only assumptions or force inconsistent accounting between escrowed ETH and emitted bridge events
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz maximal numbers and assert that every accepted numeric value preserves its economic meaning across all intermediate representations. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
