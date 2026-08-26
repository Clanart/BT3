# Q1857: mWOM.deposit - totalConverted counts requested amounts, not backed amounts

## Question
Consider wombat/mWOM.sol, where totalConverted is incremented by _amount on every path including the non-converting deposit branch, so it cannot be used to reconcile supply against veWOM backing. Assuming an owner funding transfer of MGP is sitting in the mempool, can an unprivileged attacker turn this into a divergence between `totalConverted` and `totalAccumulated` via `deposit(uint256 _amount)`, breaking the invariant that the accounting counter used to reason about backing must only count value actually committed and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: totalConverted counts requested amounts, not backed amounts)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: totalConverted is incremented by _amount on every path including the non-converting deposit branch, so it cannot be used to reconcile supply against veWOM backing. Precondition: an owner funding transfer of MGP is sitting in the mempool.
- Invariant to test: the accounting counter used to reason about backing must only count value actually committed; concretely, `totalConverted` must stay reconciled with `totalAccumulated`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `deposit(uint256 _amount)` sequence atomically under an owner funding transfer of MGP is sitting in the mempool, asserting at the end that `totalConverted` still equals `totalAccumulated` and the PoC's balance delta is non-positive.
