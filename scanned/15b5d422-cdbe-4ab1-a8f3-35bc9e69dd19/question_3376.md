# Q3376: mWOM.deposit - totalConverted counts requested amounts, not backed amounts

## Question
wombat/mWOM.sol: totalConverted is incremented by _amount on every path including the non-converting deposit branch, so it cannot be used to reconcile supply against veWOM backing. With _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked under attacker control and the veWOM mint returns less than the WOM supplied because of the lockDays curve, can an unprivileged caller sequence `deposit(uint256 _amount)` so that `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` and `IERC20(mgp).balanceOf(address(this))` no longer reconcile, violating the invariant that the accounting counter used to reason about backing must only count value actually committed and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: totalConverted counts requested amounts, not backed amounts)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: totalConverted is incremented by _amount on every path including the non-converting deposit branch, so it cannot be used to reconcile supply against veWOM backing. Precondition: the veWOM mint returns less than the WOM supplied because of the lockDays curve.
- Invariant to test: the accounting counter used to reason about backing must only count value actually committed; concretely, `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the veWOM mint returns less than the WOM supplied because of the lockDays curve, snapshot `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` and `IERC20(mgp).balanceOf(address(this))`, run the attacker's `deposit(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
