# Q1062: WombatStaking.convertAllWom - convertAllWom sweeps WOM that is mid-flight for another accounting step

## Question
wombat/WombatStaking.sol: convertAllWom() calls this.convertWOM(IERC20(wom).balanceOf(address(this))) on the whole balance, so WOM that arrived for a fee split, a pending mWOM conversion or a harvest step is locked into veWOM before the step that was going to account for it runs. Under the contract is holding WOM collected as a protocol fee that has not yet been split, is there an unprivileged sequence of `convertAllWom()` that leaves `totalAccumulated in mWOM` unreconciled with `veWom balance of WombatStaking`, violates the invariant that WOM already earmarked by an in-flight accounting step must not be sweepable by an unrelated caller, and delivers Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertAllWom()` (mechanism: convertAllWom sweeps WOM that is mid-flight for another accounting step)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAllWom()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the entire WOM balance of the contract is swept into veWOM
- Exploit idea: convertAllWom() calls this.convertWOM(IERC20(wom).balanceOf(address(this))) on the whole balance, so WOM that arrived for a fee split, a pending mWOM conversion or a harvest step is locked into veWOM before the step that was going to account for it runs. Precondition: the contract is holding WOM collected as a protocol fee that has not yet been split.
- Invariant to test: WOM already earmarked by an in-flight accounting step must not be sweepable by an unrelated caller; concretely, `totalAccumulated in mWOM` must stay reconciled with `veWom balance of WombatStaking`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which the entire WOM balance of the contract is swept into veWOM) under the contract is holding WOM collected as a protocol fee that has not yet been split, asserting on every row that WOM already earmarked by an in-flight accounting step must not be sweepable by an unrelated caller.
