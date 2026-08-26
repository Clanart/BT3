# Q2478: mWOM.incentiveDeposit - incentiveDeposit has no cap on the MGP it pays out

## Question
In wombat/mWOM.sol, incentiveDeposit() computes vlMGPAmount = _amount * rewardRatio / DENOMINATOR with no per-user limit, no global budget and no remaining-balance check, so a single caller sizing _amount large enough claims the entire MGP balance of the contract as locked vlMGP. Can an unprivileged attacker reach this through `incentiveDeposit(uint256 _amount, bool _stake)` while wombatStaking is holding WOM from an earlier deposit that has not been locked, and drive `totalConverted` out of agreement with `totalAccumulated` - breaking the invariant that an incentive budget must be bounded per caller and in aggregate, and must not be drainable in one transaction - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: incentiveDeposit has no cap on the MGP it pays out)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: incentiveDeposit() computes vlMGPAmount = _amount * rewardRatio / DENOMINATOR with no per-user limit, no global budget and no remaining-balance check, so a single caller sizing _amount large enough claims the entire MGP balance of the contract as locked vlMGP. Precondition: wombatStaking is holding WOM from an earlier deposit that has not been locked.
- Invariant to test: an incentive budget must be bounded per caller and in aggregate, and must not be drainable in one transaction; concretely, `totalConverted` must stay reconciled with `totalAccumulated`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `incentiveDeposit(uint256 _amount, bool _stake)`: constrain the setup so that wombatStaking is holding WOM from an earlier deposit that has not been locked, fuzz the attacker inputs (_amount with no cap, and _stake, while rewardRatio is non-zero), and assert after every call that an incentive budget must be bounded per caller and in aggregate, and must not be drainable in one transaction.
