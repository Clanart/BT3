# Q3833: mWOM.incentiveDeposit - incentiveDeposit costs the attacker nothing because the WOM leg is 1:1

## Question
wombat/mWOM.sol - incentiveDeposit() calls _convert(_amount, _stake, false), which mints mWOM at exactly 1:1 for the WOM supplied, so the caller retains full value on the deposit leg and the vlMGP bonus is pure profit paid from the contract's MGP. Can an unprivileged attacker controlling _amount with no cap, and _stake, while rewardRatio is non-zero, under helper is set to a SimplePoolHelper and the attacker uses convertAndStake, exploit this through `incentiveDeposit(uint256 _amount, bool _stake)` to break the reconciliation between `rewardRatio` and `DENOMINATOR` and the invariant that an incentive must cost the claimer something the protocol keeps; a 1:1 redeemable leg makes the bonus unbacked, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: incentiveDeposit costs the attacker nothing because the WOM leg is 1:1)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: incentiveDeposit() calls _convert(_amount, _stake, false), which mints mWOM at exactly 1:1 for the WOM supplied, so the caller retains full value on the deposit leg and the vlMGP bonus is pure profit paid from the contract's MGP. Precondition: helper is set to a SimplePoolHelper and the attacker uses convertAndStake.
- Invariant to test: an incentive must cost the claimer something the protocol keeps; a 1:1 redeemable leg makes the bonus unbacked; concretely, `rewardRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under helper is set to a SimplePoolHelper and the attacker uses convertAndStake, then assert `rewardRatio` and `DENOMINATOR` end identical in both runs.
