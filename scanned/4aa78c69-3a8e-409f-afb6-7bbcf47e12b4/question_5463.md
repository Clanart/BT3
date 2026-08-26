# Q5463: WombatStaking.convertWOM - convertAllWom sweeps WOM that is mid-flight for another accounting step

## Question
wombat/WombatStaking.sol: convertAllWom() calls this.convertWOM(IERC20(wom).balanceOf(address(this))) on the whole balance, so WOM that arrived for a fee split, a pending mWOM conversion or a harvest step is locked into veWOM before the step that was going to account for it runs. Under the veWOM contract leaves a non-zero allowance after mint, is there an unprivileged sequence of `convertWOM(uint256 _amount)` that leaves `IERC20(wom).balanceOf(address(this))` unreconciled with `totalConverted in mWOM`, violates the invariant that WOM already earmarked by an in-flight accounting step must not be sweepable by an unrelated caller, and delivers Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: convertAllWom sweeps WOM that is mid-flight for another accounting step)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: convertAllWom() calls this.convertWOM(IERC20(wom).balanceOf(address(this))) on the whole balance, so WOM that arrived for a fee split, a pending mWOM conversion or a harvest step is locked into veWOM before the step that was going to account for it runs. Precondition: the veWOM contract leaves a non-zero allowance after mint.
- Invariant to test: WOM already earmarked by an in-flight accounting step must not be sweepable by an unrelated caller; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted in mWOM`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Foundry fork test against the deployed pool: set up the veWOM contract leaves a non-zero allowance after mint, snapshot `IERC20(wom).balanceOf(address(this))` and `totalConverted in mWOM`, run the attacker's `convertWOM(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
