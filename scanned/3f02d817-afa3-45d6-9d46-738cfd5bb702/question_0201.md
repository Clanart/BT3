# Q0201: BNBZapper.zapInToken - safeApprove without reset on the router

## Question
In rewards/BNBZapper.sol, zapInToken() calls IERC20(fromToken).safeApprove(address(ROUTER), amount) with no prior zeroing, so any allowance residue left by a router that under-consumes permanently disables zapping for that token. Can an unprivileged attacker reach this through `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` while routePairAddresses is unset for the token so a direct two-hop path is used, and drive `minRec supplied by the caller` out of agreement with `amounts[amounts.length - 1] returned by the router` - breaking the invariant that an approval on a repeated swap path must be idempotent - for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: safeApprove without reset on the router)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: zapInToken() calls IERC20(fromToken).safeApprove(address(ROUTER), amount) with no prior zeroing, so any allowance residue left by a router that under-consumes permanently disables zapping for that token. Precondition: routePairAddresses is unset for the token so a direct two-hop path is used.
- Invariant to test: an approval on a repeated swap path must be idempotent; concretely, `minRec supplied by the caller` must stay reconciled with `amounts[amounts.length - 1] returned by the router`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Two-account fork test (victim and attacker): establish routePairAddresses is unset for the token so a direct two-hop path is used, have the attacker run `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`, then assert the victim's claimable value and the `minRec supplied by the caller` versus `amounts[amounts.length - 1] returned by the router` relation are unchanged by the attacker's transaction.
