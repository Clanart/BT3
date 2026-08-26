# Q5487: WombatStaking.convertAllWom - convertAllWom sweeps WOM that is mid-flight for another accounting step

## Question
In wombat/WombatStaking.sol, convertAllWom() calls this.convertWOM(IERC20(wom).balanceOf(address(this))) on the whole balance, so WOM that arrived for a fee split, a pending mWOM conversion or a harvest step is locked into veWOM before the step that was going to account for it runs. Can an unprivileged attacker reach this through `convertAllWom()` while the veWOM contract leaves a non-zero allowance after mint, and drive `feeInfos[i].value` out of agreement with `totalFee` - breaking the invariant that WOM already earmarked by an in-flight accounting step must not be sweepable by an unrelated caller - for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertAllWom()` (mechanism: convertAllWom sweeps WOM that is mid-flight for another accounting step)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAllWom()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the entire WOM balance of the contract is swept into veWOM
- Exploit idea: convertAllWom() calls this.convertWOM(IERC20(wom).balanceOf(address(this))) on the whole balance, so WOM that arrived for a fee split, a pending mWOM conversion or a harvest step is locked into veWOM before the step that was going to account for it runs. Precondition: the veWOM contract leaves a non-zero allowance after mint.
- Invariant to test: WOM already earmarked by an in-flight accounting step must not be sweepable by an unrelated caller; concretely, `feeInfos[i].value` must stay reconciled with `totalFee`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the veWOM contract leaves a non-zero allowance after mint, then assert `feeInfos[i].value` and `totalFee` end identical in both runs.
