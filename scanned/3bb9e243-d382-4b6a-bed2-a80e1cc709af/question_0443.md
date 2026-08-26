# Q0443: mWOM.deposit - totalConverted counts requested amounts, not backed amounts

## Question
wombat/mWOM.sol - totalConverted is incremented by _amount on every path including the non-converting deposit branch, so it cannot be used to reconcile supply against veWOM backing. Can an unprivileged attacker controlling _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked, under rewardRatio has been switched on and the contract holds a freshly funded MGP balance, exploit this through `deposit(uint256 _amount)` to break the reconciliation between `rewardRatio` and `DENOMINATOR` and the invariant that the accounting counter used to reason about backing must only count value actually committed, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: totalConverted counts requested amounts, not backed amounts)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: totalConverted is incremented by _amount on every path including the non-converting deposit branch, so it cannot be used to reconcile supply against veWOM backing. Precondition: rewardRatio has been switched on and the contract holds a freshly funded MGP balance.
- Invariant to test: the accounting counter used to reason about backing must only count value actually committed; concretely, `rewardRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `deposit(uint256 _amount)`: constrain the setup so that rewardRatio has been switched on and the contract holds a freshly funded MGP balance, fuzz the attacker inputs (_amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked), and assert after every call that the accounting counter used to reason about backing must only count value actually committed.
