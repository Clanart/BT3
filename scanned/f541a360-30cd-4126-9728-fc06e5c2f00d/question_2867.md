# Q2867: mWOM.deposit - rewardRatio is explicitly allowed to exceed one hundred percent

## Question
Note that in wombat/mWOM.sol, the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Can an attacker holding only tokens bought on market reach it via `deposit(uint256 _amount)` under the attacker calls convertAllWom on WombatStaking in the same transaction and force `_amount minted as mWOM` apart from `mintedVeWomAmount returned by IWombatStaking.convertWOM`, breaking the invariant that the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding for Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: rewardRatio is explicitly allowed to exceed one hundred percent)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Precondition: the attacker calls convertAllWom on WombatStaking in the same transaction.
- Invariant to test: the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding; concretely, `_amount minted as mWOM` must stay reconciled with `mintedVeWomAmount returned by IWombatStaking.convertWOM`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `deposit(uint256 _amount)`: constrain the setup so that the attacker calls convertAllWom on WombatStaking in the same transaction, fuzz the attacker inputs (_amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked), and assert after every call that the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding.
