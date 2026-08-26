# Q1833: WombatStaking.convertAllWom - convertAllWom sweeps WOM that is mid-flight for another accounting step

## Question
Consider wombat/WombatStaking.sol, where convertAllWom() calls this.convertWOM(IERC20(wom).balanceOf(address(this))) on the whole balance, so WOM that arrived for a fee split, a pending mWOM conversion or a harvest step is locked into veWOM before the step that was going to account for it runs. Assuming a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert, can an unprivileged attacker turn this into a divergence between `IERC20(wom).balanceOf(address(this))` and `totalConverted in mWOM` via `convertAllWom()`, breaking the invariant that WOM already earmarked by an in-flight accounting step must not be sweepable by an unrelated caller and producing Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertAllWom()` (mechanism: convertAllWom sweeps WOM that is mid-flight for another accounting step)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAllWom()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the entire WOM balance of the contract is swept into veWOM
- Exploit idea: convertAllWom() calls this.convertWOM(IERC20(wom).balanceOf(address(this))) on the whole balance, so WOM that arrived for a fee split, a pending mWOM conversion or a harvest step is locked into veWOM before the step that was going to account for it runs. Precondition: a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert.
- Invariant to test: WOM already earmarked by an in-flight accounting step must not be sweepable by an unrelated caller; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted in mWOM`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert, call `convertAllWom()`, and assert `IERC20(wom).balanceOf(address(this))` equals `totalConverted in mWOM` and that no account can withdraw more than it put in.
