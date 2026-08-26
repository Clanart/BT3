# Q0945: BNBZapper.zapInToken - safeApprove without reset on the router

## Question
In rewards/BNBZapper.sol, zapInToken() calls IERC20(fromToken).safeApprove(address(ROUTER), amount) with no prior zeroing, so any allowance residue left by a router that under-consumes permanently disables zapping for that token. Can an unprivileged attacker reach this through `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` while a residual balance of the token from an earlier zap sits on the contract, and drive `minRec supplied by the caller` out of agreement with `amounts[amounts.length - 1] returned by the router` - breaking the invariant that an approval on a repeated swap path must be idempotent - for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: safeApprove without reset on the router)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: zapInToken() calls IERC20(fromToken).safeApprove(address(ROUTER), amount) with no prior zeroing, so any allowance residue left by a router that under-consumes permanently disables zapping for that token. Precondition: a residual balance of the token from an earlier zap sits on the contract.
- Invariant to test: an approval on a repeated swap path must be idempotent; concretely, `minRec supplied by the caller` must stay reconciled with `amounts[amounts.length - 1] returned by the router`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Single-transaction PoC contract executing the whole `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` sequence atomically under a residual balance of the token from an earlier zap sits on the contract, asserting at the end that `minRec supplied by the caller` still equals `amounts[amounts.length - 1] returned by the router` and the PoC's balance delta is non-positive.
