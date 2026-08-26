# Q1369: ArbWomUp2.incentiveDeposit - a redundant zero guard hides the real entry condition

## Question
wombat/ArbWomUp2.sol - the modifier already rejects a zero amount and the body then returns early on the same condition, so the two guards disagree about whether a zero deposit reverts or silently succeeds. Can an unprivileged attacker controlling _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens, under the caller crosses several tier boundaries in one deposit, exploit this through `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` to break the reconciliation between `calDoubledCounted(account)` and `rewardTier and rewardMultiplier walk` and the invariant that a guard on an entry path must have exactly one behaviour, yielding High - Theft of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: a redundant zero guard hides the real entry condition)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: the modifier already rejects a zero amount and the body then returns early on the same condition, so the two guards disagree about whether a zero deposit reverts or silently succeeds. Precondition: the caller crosses several tier boundaries in one deposit.
- Invariant to test: a guard on an entry path must have exactly one behaviour; concretely, `calDoubledCounted(account)` must stay reconciled with `rewardTier and rewardMultiplier walk`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the caller crosses several tier boundaries in one deposit, have the attacker run `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`, then assert the victim's claimable value and the `calDoubledCounted(account)` versus `rewardTier and rewardMultiplier walk` relation are unchanged by the attacker's transaction.
