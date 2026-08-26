# Q1764: mWOM.deposit - rewardRatio is explicitly allowed to exceed one hundred percent

## Question
wombat/mWOM.sol: the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Under an owner funding transfer of MGP is sitting in the mempool, is there an unprivileged sequence of `deposit(uint256 _amount)` that leaves `totalConverted` unreconciled with `totalAccumulated`, violates the invariant that the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: rewardRatio is explicitly allowed to exceed one hundred percent)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Precondition: an owner funding transfer of MGP is sitting in the mempool.
- Invariant to test: the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding; concretely, `totalConverted` must stay reconciled with `totalAccumulated`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under an owner funding transfer of MGP is sitting in the mempool, then assert `totalConverted` and `totalAccumulated` end identical in both runs.
