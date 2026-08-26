# Q4597: mWOM.incentiveDeposit - incentiveDeposit is reachable while the WOM leg is not converted

## Question
In wombat/mWOM.sol, incentiveDeposit() passes _doConvert as false, so the WOM it receives stays liquid in this contract while the vlMGP bonus is paid immediately, decoupling the incentive from any commitment. Can an unprivileged attacker reach this through `incentiveDeposit(uint256 _amount, bool _stake)` while the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance, and drive `_amount minted as mWOM` out of agreement with `mintedVeWomAmount returned by IWombatStaking.convertWOM` - breaking the invariant that an incentive paid for committing value must only be paid once the value is actually committed - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: incentiveDeposit is reachable while the WOM leg is not converted)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: incentiveDeposit() passes _doConvert as false, so the WOM it receives stays liquid in this contract while the vlMGP bonus is paid immediately, decoupling the incentive from any commitment. Precondition: the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance.
- Invariant to test: an incentive paid for committing value must only be paid once the value is actually committed; concretely, `_amount minted as mWOM` must stay reconciled with `mintedVeWomAmount returned by IWombatStaking.convertWOM`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance, snapshot `_amount minted as mWOM` and `mintedVeWomAmount returned by IWombatStaking.convertWOM`, run the attacker's `incentiveDeposit(uint256 _amount, bool _stake)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
