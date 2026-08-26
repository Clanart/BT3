# Q3785: mWOM.deposit - totalConverted counts requested amounts, not backed amounts

## Question
Consider wombat/mWOM.sol, where totalConverted is incremented by _amount on every path including the non-converting deposit branch, so it cannot be used to reconcile supply against veWOM backing. Assuming helper is set to a SimplePoolHelper and the attacker uses convertAndStake, can an unprivileged attacker turn this into a divergence between `rewardRatio` and `DENOMINATOR` via `deposit(uint256 _amount)`, breaking the invariant that the accounting counter used to reason about backing must only count value actually committed and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: totalConverted counts requested amounts, not backed amounts)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: totalConverted is incremented by _amount on every path including the non-converting deposit branch, so it cannot be used to reconcile supply against veWOM backing. Precondition: helper is set to a SimplePoolHelper and the attacker uses convertAndStake.
- Invariant to test: the accounting counter used to reason about backing must only count value actually committed; concretely, `rewardRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `deposit(uint256 _amount)` sequence atomically under helper is set to a SimplePoolHelper and the attacker uses convertAndStake, asserting at the end that `rewardRatio` still equals `DENOMINATOR` and the PoC's balance delta is non-positive.
