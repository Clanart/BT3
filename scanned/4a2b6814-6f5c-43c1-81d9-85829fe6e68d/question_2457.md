# Q2457: insert via borrow: produce a price that passes `oracle-price-legal` while bei

## Question
Can an unprivileged attacker entering through `borrow` (mainnet/contracts/market/v0-4-market.clar:1238), controlling the future mask produced by the new debt bit, drive `insert` (mainnet/contracts/market/v0-market-vault.clar:159) — which rewrites the whole registry entry for a user id — to produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude, breaking the invariant that every price is attached to the asset it was resolved for, and each asset enters the totals exactly once, and cause permanent freezing of funds?

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:159` -> `insert`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: the future mask produced by the new debt bit
- Exploit idea: `insert` rewrites the whole registry entry for a user id. Reach it through `borrow` and produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude.
- Invariant to test: every price is attached to the asset it was resolved for, and each asset enters the totals exactly once
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Snapshot every state variable `insert` touches, run `borrow` with the future mask produced by the new debt bit, recompute the invariant off-chain from the snapshot, and assert it matches the on-chain result.
