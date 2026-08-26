# Q3957: WombatStaking.convertWOM - convertAllWom sweeps WOM that is mid-flight for another accounting step

## Question
In wombat/WombatStaking.sol, convertAllWom() calls this.convertWOM(IERC20(wom).balanceOf(address(this))) on the whole balance, so WOM that arrived for a fee split, a pending mWOM conversion or a harvest step is locked into veWOM before the step that was going to account for it runs. Does `convertWOM(uint256 _amount)` let an unprivileged caller exploit that under several feeInfos entries are active at once and the harvested amount is small, so that `isPoolFeeFree[_lpToken]` diverges from `feeInfos.length`, the invariant that WOM already earmarked by an in-flight accounting step must not be sweepable by an unrelated caller is broken, and the result is Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: convertAllWom sweeps WOM that is mid-flight for another accounting step)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: convertAllWom() calls this.convertWOM(IERC20(wom).balanceOf(address(this))) on the whole balance, so WOM that arrived for a fee split, a pending mWOM conversion or a harvest step is locked into veWOM before the step that was going to account for it runs. Precondition: several feeInfos entries are active at once and the harvested amount is small.
- Invariant to test: WOM already earmarked by an in-flight accounting step must not be sweepable by an unrelated caller; concretely, `isPoolFeeFree[_lpToken]` must stay reconciled with `feeInfos.length`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange several feeInfos entries are active at once and the harvested amount is small, call `convertWOM(uint256 _amount)`, and assert `isPoolFeeFree[_lpToken]` equals `feeInfos.length` and that no account can withdraw more than it put in.
