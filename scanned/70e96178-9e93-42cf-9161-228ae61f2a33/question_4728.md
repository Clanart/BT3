# Q4728: WombatStaking.convertWOM - convertAllWom sweeps WOM that is mid-flight for another accounting step

## Question
In wombat/WombatStaking.sol, convertAllWom() calls this.convertWOM(IERC20(wom).balanceOf(address(this))) on the whole balance, so WOM that arrived for a fee split, a pending mWOM conversion or a harvest step is locked into veWOM before the step that was going to account for it runs. Can an unprivileged attacker reach this through `convertWOM(uint256 _amount)` while the attacker deposits and withdraws through the same helper inside one transaction, and drive `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` out of agreement with `_liquidity burned from the receipt token` - breaking the invariant that WOM already earmarked by an in-flight accounting step must not be sweepable by an unrelated caller - for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: convertAllWom sweeps WOM that is mid-flight for another accounting step)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: convertAllWom() calls this.convertWOM(IERC20(wom).balanceOf(address(this))) on the whole balance, so WOM that arrived for a fee split, a pending mWOM conversion or a harvest step is locked into veWOM before the step that was going to account for it runs. Precondition: the attacker deposits and withdraws through the same helper inside one transaction.
- Invariant to test: WOM already earmarked by an in-flight accounting step must not be sweepable by an unrelated caller; concretely, `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` must stay reconciled with `_liquidity burned from the receipt token`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Single-transaction PoC contract executing the whole `convertWOM(uint256 _amount)` sequence atomically under the attacker deposits and withdraws through the same helper inside one transaction, asserting at the end that `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` still equals `_liquidity burned from the receipt token` and the PoC's balance delta is non-positive.
