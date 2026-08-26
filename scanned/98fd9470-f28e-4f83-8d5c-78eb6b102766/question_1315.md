# Q1315: ArbWomUp3.incentiveDeposit - safeApprove without reset on the vlMGP reward leg

## Question
In wombat/ArbWomUp3.sol, incentiveDeposit() calls IERC20(mgp).safeApprove(address(vlMGP), rewardToSend) with no prior zeroing, so residue from a lockFor that under-consumes permanently disables the incentive for every participant. Can an unprivileged attacker reach this through `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` while the MGP balance is below twice the capped reward, and drive `bracketRewarded` out of agreement with `calDoubledCounted(account)` - breaking the invariant that an approval on a repeated path must be idempotent - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: safeApprove without reset on the vlMGP reward leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: incentiveDeposit() calls IERC20(mgp).safeApprove(address(vlMGP), rewardToSend) with no prior zeroing, so residue from a lockFor that under-consumes permanently disables the incentive for every participant. Precondition: the MGP balance is below twice the capped reward.
- Invariant to test: an approval on a repeated path must be idempotent; concretely, `bracketRewarded` must stay reconciled with `calDoubledCounted(account)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the MGP balance is below twice the capped reward, snapshot `bracketRewarded` and `calDoubledCounted(account)`, run the attacker's `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
