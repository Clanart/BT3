# Q4168: mWOM.deposit - totalConverted counts requested amounts, not backed amounts

## Question
wombat/mWOM.sol: totalConverted is incremented by _amount on every path including the non-converting deposit branch, so it cannot be used to reconcile supply against veWOM backing. With _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked under attacker control and helper is unset so convertAndStake reverts and only the plain mint path is reachable, can an unprivileged caller sequence `deposit(uint256 _amount)` so that `IERC20(wom).balanceOf(address(this))` and `totalConverted` no longer reconcile, violating the invariant that the accounting counter used to reason about backing must only count value actually committed and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: totalConverted counts requested amounts, not backed amounts)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: totalConverted is incremented by _amount on every path including the non-converting deposit branch, so it cannot be used to reconcile supply against veWOM backing. Precondition: helper is unset so convertAndStake reverts and only the plain mint path is reachable.
- Invariant to test: the accounting counter used to reason about backing must only count value actually committed; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under helper is unset so convertAndStake reverts and only the plain mint path is reachable, then assert `IERC20(wom).balanceOf(address(this))` and `totalConverted` end identical in both runs.
