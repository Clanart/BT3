# Q0542: BNBZapper.zapInToken - no per-caller accounting at all

## Question
In rewards/BNBZapper.sol, the contract holds no ledger of who supplied what, so any token balance on it is indistinguishable from any other and the next call operates against a shared pot. Starting from a state where routePairAddresses points at a pair with no meaningful liquidity, can an unprivileged EOA use `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` to leave `routePairAddresses[token]` inconsistent with `the path built by _findRouteToBnb`, violating the invariant that a contract that holds user value even transiently must attribute it per account and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: no per-caller accounting at all)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: the contract holds no ledger of who supplied what, so any token balance on it is indistinguishable from any other and the next call operates against a shared pot. Precondition: routePairAddresses points at a pair with no meaningful liquidity.
- Invariant to test: a contract that holds user value even transiently must attribute it per account; concretely, `routePairAddresses[token]` must stay reconciled with `the path built by _findRouteToBnb`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under routePairAddresses points at a pair with no meaningful liquidity, then assert `routePairAddresses[token]` and `the path built by _findRouteToBnb` end identical in both runs.
