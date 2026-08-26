# Q2943: mWOM.deposit - totalConverted counts requested amounts, not backed amounts

## Question
In wombat/mWOM.sol, totalConverted is incremented by _amount on every path including the non-converting deposit branch, so it cannot be used to reconcile supply against veWOM backing. Can an unprivileged attacker reach this through `deposit(uint256 _amount)` while the attacker calls convertAllWom on WombatStaking in the same transaction, and drive `_amount minted as mWOM` out of agreement with `mintedVeWomAmount returned by IWombatStaking.convertWOM` - breaking the invariant that the accounting counter used to reason about backing must only count value actually committed - for Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: totalConverted counts requested amounts, not backed amounts)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: totalConverted is incremented by _amount on every path including the non-converting deposit branch, so it cannot be used to reconcile supply against veWOM backing. Precondition: the attacker calls convertAllWom on WombatStaking in the same transaction.
- Invariant to test: the accounting counter used to reason about backing must only count value actually committed; concretely, `_amount minted as mWOM` must stay reconciled with `mintedVeWomAmount returned by IWombatStaking.convertWOM`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the attacker calls convertAllWom on WombatStaking in the same transaction, snapshot `_amount minted as mWOM` and `mintedVeWomAmount returned by IWombatStaking.convertWOM`, run the attacker's `deposit(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
