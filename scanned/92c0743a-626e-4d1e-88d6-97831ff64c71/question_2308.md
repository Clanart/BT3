# Q2308: ArbWomUp3.incentiveDeposit - bracketRewarded exists but is not the basis of the correction

## Question
Note that in wombat/ArbWomUp3.sol, the contract keeps a bracketRewarded record while calDoubledCounted derives the correction from the live mWomSV balance instead, so the stored record and the applied correction can disagree. Can an attacker holding only tokens bought on market reach it via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` under a residual mWOM balance from an earlier call sits on the contract and force `IERC20(mWom).balanceOf(address(this))` apart from `the amount locked for _account in mode two`, breaking the invariant that the stored record of what has been rewarded must be the single basis of the correction for Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: bracketRewarded exists but is not the basis of the correction)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: the contract keeps a bracketRewarded record while calDoubledCounted derives the correction from the live mWomSV balance instead, so the stored record and the applied correction can disagree. Precondition: a residual mWOM balance from an earlier call sits on the contract.
- Invariant to test: the stored record of what has been rewarded must be the single basis of the correction; concretely, `IERC20(mWom).balanceOf(address(this))` must stay reconciled with `the amount locked for _account in mode two`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up a residual mWOM balance from an earlier call sits on the contract, snapshot `IERC20(mWom).balanceOf(address(this))` and `the amount locked for _account in mode two`, run the attacker's `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
