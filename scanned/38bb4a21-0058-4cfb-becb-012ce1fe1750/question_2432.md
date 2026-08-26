# Q2432: mWOM.deposit - totalConverted counts requested amounts, not backed amounts

## Question
wombat/mWOM.sol: totalConverted is incremented by _amount on every path including the non-converting deposit branch, so it cannot be used to reconcile supply against veWOM backing. With _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked under attacker control and wombatStaking is holding WOM from an earlier deposit that has not been locked, can an unprivileged caller sequence `deposit(uint256 _amount)` so that `IERC20(this).totalSupply()` and `IERC20(wom).balanceOf(wombatStaking) + veWom backing` no longer reconcile, violating the invariant that the accounting counter used to reason about backing must only count value actually committed and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: totalConverted counts requested amounts, not backed amounts)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: totalConverted is incremented by _amount on every path including the non-converting deposit branch, so it cannot be used to reconcile supply against veWOM backing. Precondition: wombatStaking is holding WOM from an earlier deposit that has not been locked.
- Invariant to test: the accounting counter used to reason about backing must only count value actually committed; concretely, `IERC20(this).totalSupply()` must stay reconciled with `IERC20(wom).balanceOf(wombatStaking) + veWom backing`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked) under wombatStaking is holding WOM from an earlier deposit that has not been locked, asserting on every row that the accounting counter used to reason about backing must only count value actually committed.
