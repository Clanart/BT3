# Q5264: WombatStaking.convertWOM - convertAllWom sweeps WOM that is mid-flight for another accounting step

## Question
In wombat/WombatStaking.sol, convertAllWom() calls this.convertWOM(IERC20(wom).balanceOf(address(this))) on the whole balance, so WOM that arrived for a fee split, a pending mWOM conversion or a harvest step is locked into veWOM before the step that was going to account for it runs. Can an unprivileged attacker reach this through `convertWOM(uint256 _amount)` while the bonus reward token registered for the asset is also one of the fee currencies, and drive `totalAccumulated in mWOM` out of agreement with `veWom balance of WombatStaking` - breaking the invariant that WOM already earmarked by an in-flight accounting step must not be sweepable by an unrelated caller - for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: convertAllWom sweeps WOM that is mid-flight for another accounting step)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: convertAllWom() calls this.convertWOM(IERC20(wom).balanceOf(address(this))) on the whole balance, so WOM that arrived for a fee split, a pending mWOM conversion or a harvest step is locked into veWOM before the step that was going to account for it runs. Precondition: the bonus reward token registered for the asset is also one of the fee currencies.
- Invariant to test: WOM already earmarked by an in-flight accounting step must not be sweepable by an unrelated caller; concretely, `totalAccumulated in mWOM` must stay reconciled with `veWom balance of WombatStaking`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the bonus reward token registered for the asset is also one of the fee currencies, then assert `totalAccumulated in mWOM` and `veWom balance of WombatStaking` end identical in both runs.
