# Q0039: WombatStaking.convertWOM - convertAllWom sweeps WOM that is mid-flight for another accounting step

## Question
wombat/WombatStaking.sol: convertAllWom() calls this.convertWOM(IERC20(wom).balanceOf(address(this))) on the whole balance, so WOM that arrived for a fee split, a pending mWOM conversion or a harvest step is locked into veWOM before the step that was going to account for it runs. Under the contract is holding WOM that mWOM._convert has just transferred in but not yet locked, is there an unprivileged sequence of `convertWOM(uint256 _amount)` that leaves `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` unreconciled with `_liquidity burned from the receipt token`, violates the invariant that WOM already earmarked by an in-flight accounting step must not be sweepable by an unrelated caller, and delivers Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: convertAllWom sweeps WOM that is mid-flight for another accounting step)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: convertAllWom() calls this.convertWOM(IERC20(wom).balanceOf(address(this))) on the whole balance, so WOM that arrived for a fee split, a pending mWOM conversion or a harvest step is locked into veWOM before the step that was going to account for it runs. Precondition: the contract is holding WOM that mWOM._convert has just transferred in but not yet locked.
- Invariant to test: WOM already earmarked by an in-flight accounting step must not be sweepable by an unrelated caller; concretely, `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` must stay reconciled with `_liquidity burned from the receipt token`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Single-transaction PoC contract executing the whole `convertWOM(uint256 _amount)` sequence atomically under the contract is holding WOM that mWOM._convert has just transferred in but not yet locked, asserting at the end that `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` still equals `_liquidity burned from the receipt token` and the PoC's balance delta is non-positive.
