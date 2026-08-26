# Q0387: BNBZapper.zapInToken - safeApprove without reset on the router

## Question
Consider rewards/BNBZapper.sol, where zapInToken() calls IERC20(fromToken).safeApprove(address(ROUTER), amount) with no prior zeroing, so any allowance residue left by a router that under-consumes permanently disables zapping for that token. Assuming routePairAddresses points at a pair with no meaningful liquidity, can an unprivileged attacker turn this into a divergence between `IERC20(fromToken).balanceOf(address(this))` and `amount pulled from msg.sender` via `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`, breaking the invariant that an approval on a repeated swap path must be idempotent and producing High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: safeApprove without reset on the router)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: zapInToken() calls IERC20(fromToken).safeApprove(address(ROUTER), amount) with no prior zeroing, so any allowance residue left by a router that under-consumes permanently disables zapping for that token. Precondition: routePairAddresses points at a pair with no meaningful liquidity.
- Invariant to test: an approval on a repeated swap path must be idempotent; concretely, `IERC20(fromToken).balanceOf(address(this))` must stay reconciled with `amount pulled from msg.sender`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under routePairAddresses points at a pair with no meaningful liquidity, then assert `IERC20(fromToken).balanceOf(address(this))` and `amount pulled from msg.sender` end identical in both runs.
