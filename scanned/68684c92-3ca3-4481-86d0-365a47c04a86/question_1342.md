# Q1342: ArbWomUp2.incentiveDeposit - the reward is computed before the deposit is recorded

## Question
In wombat/ArbWomUp2.sol, incentiveDeposit() reads this.getRewardAmount(_amount, msg.sender) before _deposit(_amount) updates userWOMDeposited, so the tier input and the deposit record are taken from different views. Can an unprivileged attacker reach this through `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` while the caller crosses several tier boundaries in one deposit, and drive `bullBonusRatio` out of agreement with `DENOMINATOR` - breaking the invariant that the tier input and the deposit record must be derived from one snapshot - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: the reward is computed before the deposit is recorded)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: incentiveDeposit() reads this.getRewardAmount(_amount, msg.sender) before _deposit(_amount) updates userWOMDeposited, so the tier input and the deposit record are taken from different views. Precondition: the caller crosses several tier boundaries in one deposit.
- Invariant to test: the tier input and the deposit record must be derived from one snapshot; concretely, `bullBonusRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the caller crosses several tier boundaries in one deposit, have the attacker run `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`, then assert the victim's claimable value and the `bullBonusRatio` versus `DENOMINATOR` relation are unchanged by the attacker's transaction.
