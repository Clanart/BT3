# Q0368: ArbWomUp3.incentiveDeposit - bracketRewarded exists but is not the basis of the correction

## Question
Consider wombat/ArbWomUp3.sol, where the contract keeps a bracketRewarded record while calDoubledCounted derives the correction from the live mWomSV balance instead, so the stored record and the applied correction can disagree. Assuming the caller deposits, calls mWomSV.startUnlock and deposits again inside the same block, can an unprivileged attacker turn this into a divergence between `IERC20(mWom).balanceOf(address(this))` and `the amount locked for _account in mode two` via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, breaking the invariant that the stored record of what has been rewarded must be the single basis of the correction and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: bracketRewarded exists but is not the basis of the correction)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: the contract keeps a bracketRewarded record while calDoubledCounted derives the correction from the live mWomSV balance instead, so the stored record and the applied correction can disagree. Precondition: the caller deposits, calls mWomSV.startUnlock and deposits again inside the same block.
- Invariant to test: the stored record of what has been rewarded must be the single basis of the correction; concretely, `IERC20(mWom).balanceOf(address(this))` must stay reconciled with `the amount locked for _account in mode two`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the caller deposits, calls mWomSV.startUnlock and deposits again inside the same block, snapshot `IERC20(mWom).balanceOf(address(this))` and `the amount locked for _account in mode two`, run the attacker's `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
