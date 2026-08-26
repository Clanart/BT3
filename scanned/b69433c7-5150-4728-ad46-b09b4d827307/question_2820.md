# Q2820: ArbWomUp3.incentiveDeposit - bracketRewarded exists but is not the basis of the correction

## Question
Note that in wombat/ArbWomUp3.sol, the contract keeps a bracketRewarded record while calDoubledCounted derives the correction from the live mWomSV balance instead, so the stored record and the applied correction can disagree. Can an attacker holding only tokens bought on market reach it via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` under the caller crosses several tier boundaries in one deposit and force `rewardToSend after the _mode == 2 doubling` apart from `the mgpleft cap applied inside getRewardAmount`, breaking the invariant that the stored record of what has been rewarded must be the single basis of the correction for Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: bracketRewarded exists but is not the basis of the correction)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: the contract keeps a bracketRewarded record while calDoubledCounted derives the correction from the live mWomSV balance instead, so the stored record and the applied correction can disagree. Precondition: the caller crosses several tier boundaries in one deposit.
- Invariant to test: the stored record of what has been rewarded must be the single basis of the correction; concretely, `rewardToSend after the _mode == 2 doubling` must stay reconciled with `the mgpleft cap applied inside getRewardAmount`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the caller crosses several tier boundaries in one deposit, then assert `rewardToSend after the _mode == 2 doubling` and `the mgpleft cap applied inside getRewardAmount` end identical in both runs.
