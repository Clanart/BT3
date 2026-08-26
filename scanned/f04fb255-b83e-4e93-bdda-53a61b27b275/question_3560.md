# Q3560: WombatStaking.convertAllWom - convertAllWom sweeps WOM that is mid-flight for another accounting step

## Question
wombat/WombatStaking.sol: convertAllWom() calls this.convertWOM(IERC20(wom).balanceOf(address(this))) on the whole balance, so WOM that arrived for a fee split, a pending mWOM conversion or a harvest step is locked into veWOM before the step that was going to account for it runs. With the exact block at which the entire WOM balance of the contract is swept into veWOM under attacker control and the pool is marked isPoolFeeFree so the fee loop is skipped entirely, can an unprivileged caller sequence `convertAllWom()` so that `isPoolFeeFree[_lpToken]` and `feeInfos.length` no longer reconcile, violating the invariant that WOM already earmarked by an in-flight accounting step must not be sweepable by an unrelated caller and realising Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertAllWom()` (mechanism: convertAllWom sweeps WOM that is mid-flight for another accounting step)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAllWom()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the entire WOM balance of the contract is swept into veWOM
- Exploit idea: convertAllWom() calls this.convertWOM(IERC20(wom).balanceOf(address(this))) on the whole balance, so WOM that arrived for a fee split, a pending mWOM conversion or a harvest step is locked into veWOM before the step that was going to account for it runs. Precondition: the pool is marked isPoolFeeFree so the fee loop is skipped entirely.
- Invariant to test: WOM already earmarked by an in-flight accounting step must not be sweepable by an unrelated caller; concretely, `isPoolFeeFree[_lpToken]` must stay reconciled with `feeInfos.length`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Invariant/fuzz run over `convertAllWom()`: constrain the setup so that the pool is marked isPoolFeeFree so the fee loop is skipped entirely, fuzz the attacker inputs (the exact block at which the entire WOM balance of the contract is swept into veWOM), and assert after every call that WOM already earmarked by an in-flight accounting step must not be sweepable by an unrelated caller.
