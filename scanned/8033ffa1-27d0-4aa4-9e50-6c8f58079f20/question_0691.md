# Q0691: mWOM.incentiveDeposit - incentiveDeposit is reachable while the WOM leg is not converted

## Question
Note that in wombat/mWOM.sol, incentiveDeposit() passes _doConvert as false, so the WOM it receives stays liquid in this contract while the vlMGP bonus is paid immediately, decoupling the incentive from any commitment. Can an attacker holding only tokens bought on market reach it via `incentiveDeposit(uint256 _amount, bool _stake)` under rewardRatio has been switched on and the contract holds a freshly funded MGP balance and force `totalConverted` apart from `totalAccumulated`, breaking the invariant that an incentive paid for committing value must only be paid once the value is actually committed for Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: incentiveDeposit is reachable while the WOM leg is not converted)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: incentiveDeposit() passes _doConvert as false, so the WOM it receives stays liquid in this contract while the vlMGP bonus is paid immediately, decoupling the incentive from any commitment. Precondition: rewardRatio has been switched on and the contract holds a freshly funded MGP balance.
- Invariant to test: an incentive paid for committing value must only be paid once the value is actually committed; concretely, `totalConverted` must stay reconciled with `totalAccumulated`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount with no cap, and _stake, while rewardRatio is non-zero) under rewardRatio has been switched on and the contract holds a freshly funded MGP balance, asserting on every row that an incentive paid for committing value must only be paid once the value is actually committed.
