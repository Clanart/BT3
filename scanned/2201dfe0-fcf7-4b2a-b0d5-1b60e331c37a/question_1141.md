# Q1141: ArbWomUp2.incentiveDeposit - a redundant zero guard hides the real entry condition

## Question
In wombat/ArbWomUp2.sol, the modifier already rejects a zero amount and the body then returns early on the same condition, so the two guards disagree about whether a zero deposit reverts or silently succeeds. Can an unprivileged attacker reach this through `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` while the caller splits the deposit across several addresses, and drive `bullBonusRatio` out of agreement with `DENOMINATOR` - breaking the invariant that a guard on an entry path must have exactly one behaviour - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: a redundant zero guard hides the real entry condition)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: the modifier already rejects a zero amount and the body then returns early on the same condition, so the two guards disagree about whether a zero deposit reverts or silently succeeds. Precondition: the caller splits the deposit across several addresses.
- Invariant to test: a guard on an entry path must have exactly one behaviour; concretely, `bullBonusRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the caller splits the deposit across several addresses, snapshot `bullBonusRatio` and `DENOMINATOR`, run the attacker's `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
