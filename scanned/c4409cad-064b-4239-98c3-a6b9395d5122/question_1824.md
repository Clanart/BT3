# Q1824: uint-to-list-u64 via borrow: produce a price that passes `oracle-price-legal` while bei

## Question
Does `borrow` (mainnet/contracts/market/v0-4-market.clar:1238) let an unprivileged attacker who controls `amount` reach `uint-to-list-u64` (mainnet/contracts/registry/v0-assets.clar:80) in a state where it produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude? Given that it expands a bitmap into a 64-element list, the invariant that the LTV a position is judged against belongs to the exact asset set it will hold after the call breaks and the result is permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/registry/v0-assets.clar:80` -> `uint-to-list-u64`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `uint-to-list-u64` expands a bitmap into a 64-element list. Reach it through `borrow` and produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude.
- Invariant to test: the LTV a position is judged against belongs to the exact asset set it will hold after the call
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz `amount` across its boundary values through `borrow` in simnet and assert `uint-to-list-u64` never returns a value that breaks the invariant.
