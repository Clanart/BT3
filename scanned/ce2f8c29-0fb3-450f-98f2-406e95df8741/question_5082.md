# Q5082: WombatStaking.convertWOM - veWOM lock commits pooled WOM for lockDays with no user opt-out

## Question
wombat/WombatStaking.sol: convertWOM() locks for lockDays with no per-depositor accounting, so an mWOM holder's underlying WOM sits inside a veWOM lock they never agreed to and cannot address individually. With _amount, with no upper bound and no relation to who supplied the WOM under attacker control and a large honest deposit is pending in the mempool for the same pool, can an unprivileged caller sequence `convertWOM(uint256 _amount)` so that `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` and `_liquidity burned from the receipt token` no longer reconcile, violating the invariant that the backing of a liquid wrapper must remain redeemable under terms its holders accepted and realising Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: veWOM lock commits pooled WOM for lockDays with no user opt-out)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: convertWOM() locks for lockDays with no per-depositor accounting, so an mWOM holder's underlying WOM sits inside a veWOM lock they never agreed to and cannot address individually. Precondition: a large honest deposit is pending in the mempool for the same pool.
- Invariant to test: the backing of a liquid wrapper must remain redeemable under terms its holders accepted; concretely, `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` must stay reconciled with `_liquidity burned from the receipt token`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a large honest deposit is pending in the mempool for the same pool, then assert `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` and `_liquidity burned from the receipt token` end identical in both runs.
