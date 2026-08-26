# Q5292: WombatStaking.convertAllWom - convertAllWom sweeps WOM that is mid-flight for another accounting step

## Question
In wombat/WombatStaking.sol, convertAllWom() calls this.convertWOM(IERC20(wom).balanceOf(address(this))) on the whole balance, so WOM that arrived for a fee split, a pending mWOM conversion or a harvest step is locked into veWOM before the step that was going to account for it runs. Does `convertAllWom()` let an unprivileged caller exploit that under the bonus reward token registered for the asset is also one of the fee currencies, so that `IERC20(wom).balanceOf(address(this))` diverges from `totalConverted in mWOM`, the invariant that WOM already earmarked by an in-flight accounting step must not be sweepable by an unrelated caller is broken, and the result is Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertAllWom()` (mechanism: convertAllWom sweeps WOM that is mid-flight for another accounting step)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAllWom()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the entire WOM balance of the contract is swept into veWOM
- Exploit idea: convertAllWom() calls this.convertWOM(IERC20(wom).balanceOf(address(this))) on the whole balance, so WOM that arrived for a fee split, a pending mWOM conversion or a harvest step is locked into veWOM before the step that was going to account for it runs. Precondition: the bonus reward token registered for the asset is also one of the fee currencies.
- Invariant to test: WOM already earmarked by an in-flight accounting step must not be sweepable by an unrelated caller; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted in mWOM`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the bonus reward token registered for the asset is also one of the fee currencies, then assert `IERC20(wom).balanceOf(address(this))` and `totalConverted in mWOM` end identical in both runs.
