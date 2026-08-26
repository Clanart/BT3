# Q1213: mWOM.deposit - totalConverted counts requested amounts, not backed amounts

## Question
Note that in wombat/mWOM.sol, totalConverted is incremented by _amount on every path including the non-converting deposit branch, so it cannot be used to reconcile supply against veWOM backing. Can an attacker holding only tokens bought on market reach it via `deposit(uint256 _amount)` under rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit and force `IERC20(wom).balanceOf(address(this))` apart from `totalConverted`, breaking the invariant that the accounting counter used to reason about backing must only count value actually committed for Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: totalConverted counts requested amounts, not backed amounts)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: totalConverted is incremented by _amount on every path including the non-converting deposit branch, so it cannot be used to reconcile supply against veWOM backing. Precondition: rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit.
- Invariant to test: the accounting counter used to reason about backing must only count value actually committed; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, then assert `IERC20(wom).balanceOf(address(this))` and `totalConverted` end identical in both runs.
