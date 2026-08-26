# Q4418: WombatStaking.convertAllWom - convertAllWom sweeps WOM that is mid-flight for another accounting step

## Question
In wombat/WombatStaking.sol, convertAllWom() calls this.convertWOM(IERC20(wom).balanceOf(address(this))) on the whole balance, so WOM that arrived for a fee split, a pending mWOM conversion or a harvest step is locked into veWOM before the step that was going to account for it runs. Starting from a state where the deposit token for the pool is wBNB and the helper arrived through depositNative, can an unprivileged EOA use `convertAllWom()` to leave `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` inconsistent with `_liquidity burned from the receipt token`, violating the invariant that WOM already earmarked by an in-flight accounting step must not be sweepable by an unrelated caller and extracting Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertAllWom()` (mechanism: convertAllWom sweeps WOM that is mid-flight for another accounting step)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAllWom()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the entire WOM balance of the contract is swept into veWOM
- Exploit idea: convertAllWom() calls this.convertWOM(IERC20(wom).balanceOf(address(this))) on the whole balance, so WOM that arrived for a fee split, a pending mWOM conversion or a harvest step is locked into veWOM before the step that was going to account for it runs. Precondition: the deposit token for the pool is wBNB and the helper arrived through depositNative.
- Invariant to test: WOM already earmarked by an in-flight accounting step must not be sweepable by an unrelated caller; concretely, `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` must stay reconciled with `_liquidity burned from the receipt token`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the deposit token for the pool is wBNB and the helper arrived through depositNative, call `convertAllWom()`, and assert `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` equals `_liquidity burned from the receipt token` and that no account can withdraw more than it put in.
