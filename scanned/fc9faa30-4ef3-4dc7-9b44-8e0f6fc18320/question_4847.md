# Q4847: mWOM.incentiveDeposit - rewardRatio is explicitly allowed to exceed one hundred percent

## Question
In wombat/mWOM.sol, the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Starting from a state where the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance, can an unprivileged EOA use `incentiveDeposit(uint256 _amount, bool _stake)` to leave `_amount minted as mWOM` inconsistent with `mintedVeWomAmount returned by IWombatStaking.convertWOM`, violating the invariant that the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding and extracting Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: rewardRatio is explicitly allowed to exceed one hundred percent)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Precondition: the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance.
- Invariant to test: the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding; concretely, `_amount minted as mWOM` must stay reconciled with `mintedVeWomAmount returned by IWombatStaking.convertWOM`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_amount with no cap, and _stake, while rewardRatio is non-zero) under the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance, asserting on every row that the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding.
