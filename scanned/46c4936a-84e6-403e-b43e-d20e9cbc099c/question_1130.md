# Q1130: BNBZapper.zapInToken - safeApprove without reset on the router

## Question
rewards/BNBZapper.sol: zapInToken() calls IERC20(fromToken).safeApprove(address(ROUTER), amount) with no prior zeroing, so any allowance residue left by a router that under-consumes permanently disables zapping for that token. With fromToken, amount, minRec and receiver, all unrestricted under attacker control and WombatBribeManager.previewBnbAmountForHarvest is being read by an integrator in the same block, can an unprivileged caller sequence `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` so that `IERC20(fromToken).balanceOf(address(this))` and `amount pulled from msg.sender` no longer reconcile, violating the invariant that an approval on a repeated swap path must be idempotent and realising High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: safeApprove without reset on the router)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: zapInToken() calls IERC20(fromToken).safeApprove(address(ROUTER), amount) with no prior zeroing, so any allowance residue left by a router that under-consumes permanently disables zapping for that token. Precondition: WombatBribeManager.previewBnbAmountForHarvest is being read by an integrator in the same block.
- Invariant to test: an approval on a repeated swap path must be idempotent; concretely, `IERC20(fromToken).balanceOf(address(this))` must stay reconciled with `amount pulled from msg.sender`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Unit test with mocked Wombat and router legs: arrange WombatBribeManager.previewBnbAmountForHarvest is being read by an integrator in the same block, call `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`, and assert `IERC20(fromToken).balanceOf(address(this))` equals `amount pulled from msg.sender` and that no account can withdraw more than it put in.
