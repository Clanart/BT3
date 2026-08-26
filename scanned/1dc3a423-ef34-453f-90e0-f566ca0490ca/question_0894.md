# Q0894: ArbWomUp2.incentiveDeposit - a redundant zero guard hides the real entry condition

## Question
wombat/ArbWomUp2.sol: the modifier already rejects a zero amount and the body then returns early on the same condition, so the two guards disagree about whether a zero deposit reverts or silently succeeds. With _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens under attacker control and bullBonusRatio is configured well above zero, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` so that `_minMGPRec supplied by the caller` and `the MGP actually received by the swap` no longer reconcile, violating the invariant that a guard on an entry path must have exactly one behaviour and realising High - Theft of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: a redundant zero guard hides the real entry condition)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: the modifier already rejects a zero amount and the body then returns early on the same condition, so the two guards disagree about whether a zero deposit reverts or silently succeeds. Precondition: bullBonusRatio is configured well above zero.
- Invariant to test: a guard on an entry path must have exactly one behaviour; concretely, `_minMGPRec supplied by the caller` must stay reconciled with `the MGP actually received by the swap`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` sequence atomically under bullBonusRatio is configured well above zero, asserting at the end that `_minMGPRec supplied by the caller` still equals `the MGP actually received by the swap` and the PoC's balance delta is non-positive.
