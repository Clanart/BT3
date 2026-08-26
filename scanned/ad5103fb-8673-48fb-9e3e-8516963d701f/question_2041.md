# Q2041: mWOM.incentiveDeposit - incentiveDeposit is reachable while the WOM leg is not converted

## Question
wombat/mWOM.sol - incentiveDeposit() passes _doConvert as false, so the WOM it receives stays liquid in this contract while the vlMGP bonus is paid immediately, decoupling the incentive from any commitment. Can an unprivileged attacker controlling _amount with no cap, and _stake, while rewardRatio is non-zero, under an owner funding transfer of MGP is sitting in the mempool, exploit this through `incentiveDeposit(uint256 _amount, bool _stake)` to break the reconciliation between `_amount minted as mWOM` and `mintedVeWomAmount returned by IWombatStaking.convertWOM` and the invariant that an incentive paid for committing value must only be paid once the value is actually committed, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: incentiveDeposit is reachable while the WOM leg is not converted)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: incentiveDeposit() passes _doConvert as false, so the WOM it receives stays liquid in this contract while the vlMGP bonus is paid immediately, decoupling the incentive from any commitment. Precondition: an owner funding transfer of MGP is sitting in the mempool.
- Invariant to test: an incentive paid for committing value must only be paid once the value is actually committed; concretely, `_amount minted as mWOM` must stay reconciled with `mintedVeWomAmount returned by IWombatStaking.convertWOM`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up an owner funding transfer of MGP is sitting in the mempool, snapshot `_amount minted as mWOM` and `mintedVeWomAmount returned by IWombatStaking.convertWOM`, run the attacker's `incentiveDeposit(uint256 _amount, bool _stake)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
