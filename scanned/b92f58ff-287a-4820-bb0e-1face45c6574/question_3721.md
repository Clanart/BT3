# Q3721: mWOM.deposit - rewardRatio is explicitly allowed to exceed one hundred percent

## Question
wombat/mWOM.sol - the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Can an unprivileged attacker controlling _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked, under helper is set to a SimplePoolHelper and the attacker uses convertAndStake, exploit this through `deposit(uint256 _amount)` to break the reconciliation between `rewardRatio` and `DENOMINATOR` and the invariant that the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: rewardRatio is explicitly allowed to exceed one hundred percent)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Precondition: helper is set to a SimplePoolHelper and the attacker uses convertAndStake.
- Invariant to test: the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding; concretely, `rewardRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `deposit(uint256 _amount)` sequence atomically under helper is set to a SimplePoolHelper and the attacker uses convertAndStake, asserting at the end that `rewardRatio` still equals `DENOMINATOR` and the PoC's balance delta is non-positive.
