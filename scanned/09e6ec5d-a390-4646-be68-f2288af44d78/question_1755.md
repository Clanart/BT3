# Q1755: ArbWomUp3.incentiveDeposit - bracketRewarded exists but is not the basis of the correction

## Question
In wombat/ArbWomUp3.sol, the contract keeps a bracketRewarded record while calDoubledCounted derives the correction from the live mWomSV balance instead, so the stored record and the applied correction can disagree. Does `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` let an unprivileged caller exploit that under the caller sets _convertRatio to zero so the whole leg is swapped, so that `bracketRewarded` diverges from `calDoubledCounted(account)`, the invariant that the stored record of what has been rewarded must be the single basis of the correction is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: bracketRewarded exists but is not the basis of the correction)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: the contract keeps a bracketRewarded record while calDoubledCounted derives the correction from the live mWomSV balance instead, so the stored record and the applied correction can disagree. Precondition: the caller sets _convertRatio to zero so the whole leg is swapped.
- Invariant to test: the stored record of what has been rewarded must be the single basis of the correction; concretely, `bracketRewarded` must stay reconciled with `calDoubledCounted(account)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the caller sets _convertRatio to zero so the whole leg is swapped, have the attacker run `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, then assert the victim's claimable value and the `bracketRewarded` versus `calDoubledCounted(account)` relation are unchanged by the attacker's transaction.
