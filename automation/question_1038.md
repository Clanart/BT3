# Q1038: BNBZapper.zapInToken - route path derived from mutable owner state without validation

## Question
In rewards/BNBZapper.sol, _findRouteToBnb() builds a two or three hop path purely from routePairAddresses[token] with no check that the resulting pair exists or holds liquidity, so a route through an empty pair executes at an arbitrary price. Can an unprivileged attacker reach this through `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` while a residual balance of the token from an earlier zap sits on the contract, and drive `routePairAddresses[token]` out of agreement with `the path built by _findRouteToBnb` - breaking the invariant that a routing table entry must be validated against real liquidity before value is sent through it - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: route path derived from mutable owner state without validation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: _findRouteToBnb() builds a two or three hop path purely from routePairAddresses[token] with no check that the resulting pair exists or holds liquidity, so a route through an empty pair executes at an arbitrary price. Precondition: a residual balance of the token from an earlier zap sits on the contract.
- Invariant to test: a routing table entry must be validated against real liquidity before value is sent through it; concretely, `routePairAddresses[token]` must stay reconciled with `the path built by _findRouteToBnb`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`: constrain the setup so that a residual balance of the token from an earlier zap sits on the contract, fuzz the attacker inputs (fromToken, amount, minRec and receiver, all unrestricted), and assert after every call that a routing table entry must be validated against real liquidity before value is sent through it.
