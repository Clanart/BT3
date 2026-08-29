# Q4872: refresh via borrow: apply a transform after the gate that was supposed to boun

## Question
Does `borrow` (mainnet/contracts/market/v0-4-market.clar:1238) let an unprivileged attacker who controls the `price-feeds` buffers reach `refresh` (mainnet/contracts/market/v0-market-vault.clar:171) in a state where it apply a transform after the gate that was supposed to bound its output? Given that it rewrites `mask` and stamps `last-update` to `stacks-block-time` on every write, the invariant that each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction breaks and the result is permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:171` -> `refresh`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: the `price-feeds` buffers
- Exploit idea: `refresh` rewrites `mask` and stamps `last-update` to `stacks-block-time` on every write. Reach it through `borrow` and apply a transform after the gate that was supposed to bound its output.
- Invariant to test: each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz the `price-feeds` buffers across its boundary values through `borrow` in simnet and assert `refresh` never returns a value that breaks the invariant.
