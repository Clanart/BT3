# Q0480: BNBZapper.zapInToken - route path derived from mutable owner state without validation

## Question
Consider rewards/BNBZapper.sol, where _findRouteToBnb() builds a two or three hop path purely from routePairAddresses[token] with no check that the resulting pair exists or holds liquidity, so a route through an empty pair executes at an arbitrary price. Assuming routePairAddresses points at a pair with no meaningful liquidity, can an unprivileged attacker turn this into a divergence between `minRec supplied by the caller` and `amounts[amounts.length - 1] returned by the router` via `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`, breaking the invariant that a routing table entry must be validated against real liquidity before value is sent through it and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: route path derived from mutable owner state without validation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: _findRouteToBnb() builds a two or three hop path purely from routePairAddresses[token] with no check that the resulting pair exists or holds liquidity, so a route through an empty pair executes at an arbitrary price. Precondition: routePairAddresses points at a pair with no meaningful liquidity.
- Invariant to test: a routing table entry must be validated against real liquidity before value is sent through it; concretely, `minRec supplied by the caller` must stay reconciled with `amounts[amounts.length - 1] returned by the router`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange routePairAddresses points at a pair with no meaningful liquidity, call `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`, and assert `minRec supplied by the caller` equals `amounts[amounts.length - 1] returned by the router` and that no account can withdraw more than it put in.
