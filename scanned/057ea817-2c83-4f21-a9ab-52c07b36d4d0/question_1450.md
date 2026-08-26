# Q1450: ArbWomUp3.incentiveDeposit - bracketRewarded exists but is not the basis of the correction

## Question
wombat/ArbWomUp3.sol - the contract keeps a bracketRewarded record while calDoubledCounted derives the correction from the live mWomSV balance instead, so the stored record and the applied correction can disagree. Can an unprivileged attacker controlling _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer, under the MGP balance is below twice the capped reward, exploit this through `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` to break the reconciliation between `rewardToSend` and `IERC20(mgp).balanceOf(address(this))` and the invariant that the stored record of what has been rewarded must be the single basis of the correction, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: bracketRewarded exists but is not the basis of the correction)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: the contract keeps a bracketRewarded record while calDoubledCounted derives the correction from the live mWomSV balance instead, so the stored record and the applied correction can disagree. Precondition: the MGP balance is below twice the capped reward.
- Invariant to test: the stored record of what has been rewarded must be the single basis of the correction; concretely, `rewardToSend` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the MGP balance is below twice the capped reward, snapshot `rewardToSend` and `IERC20(mgp).balanceOf(address(this))`, run the attacker's `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
