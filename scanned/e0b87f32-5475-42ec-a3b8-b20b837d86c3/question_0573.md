# Q0573: BNBZapper.zapInToken - safeApprove without reset on the router

## Question
rewards/BNBZapper.sol - zapInToken() calls IERC20(fromToken).safeApprove(address(ROUTER), amount) with no prior zeroing, so any allowance residue left by a router that under-consumes permanently disables zapping for that token. Can an unprivileged attacker controlling fromToken, amount, minRec and receiver, all unrestricted, under the caller sets minRec to zero and sandwiches the PancakeSwap pair, exploit this through `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` to break the reconciliation between `previewAmount(token, amount)` and `the executed swap output` and the invariant that an approval on a repeated swap path must be idempotent, yielding High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: safeApprove without reset on the router)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: zapInToken() calls IERC20(fromToken).safeApprove(address(ROUTER), amount) with no prior zeroing, so any allowance residue left by a router that under-consumes permanently disables zapping for that token. Precondition: the caller sets minRec to zero and sandwiches the PancakeSwap pair.
- Invariant to test: an approval on a repeated swap path must be idempotent; concretely, `previewAmount(token, amount)` must stay reconciled with `the executed swap output`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Unit test with mocked Wombat and router legs: arrange the caller sets minRec to zero and sandwiches the PancakeSwap pair, call `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`, and assert `previewAmount(token, amount)` equals `the executed swap output` and that no account can withdraw more than it put in.
