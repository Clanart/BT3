# Q4801: mWOM.deposit - totalConverted counts requested amounts, not backed amounts

## Question
Consider wombat/mWOM.sol, where totalConverted is incremented by _amount on every path including the non-converting deposit branch, so it cannot be used to reconcile supply against veWOM backing. Assuming the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance, can an unprivileged attacker turn this into a divergence between `IERC20(this).totalSupply()` and `IERC20(wom).balanceOf(wombatStaking) + veWom backing` via `deposit(uint256 _amount)`, breaking the invariant that the accounting counter used to reason about backing must only count value actually committed and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: totalConverted counts requested amounts, not backed amounts)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: totalConverted is incremented by _amount on every path including the non-converting deposit branch, so it cannot be used to reconcile supply against veWOM backing. Precondition: the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance.
- Invariant to test: the accounting counter used to reason about backing must only count value actually committed; concretely, `IERC20(this).totalSupply()` must stay reconciled with `IERC20(wom).balanceOf(wombatStaking) + veWom backing`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance, call `deposit(uint256 _amount)`, and assert `IERC20(this).totalSupply()` equals `IERC20(wom).balanceOf(wombatStaking) + veWom backing` and that no account can withdraw more than it put in.
