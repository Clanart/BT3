# Q0398: ArbWomUp2.incentiveDeposit - a redundant zero guard hides the real entry condition

## Question
wombat/ArbWomUp2.sol: the modifier already rejects a zero amount and the body then returns early on the same condition, so the two guards disagree about whether a zero deposit reverts or silently succeeds. With _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens under attacker control and the caller sets _bullMode to false so the plain transfer branch runs, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` so that `claimedReward[account]` and `userWOMDeposited[account]` no longer reconcile, violating the invariant that a guard on an entry path must have exactly one behaviour and realising High - Theft of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: a redundant zero guard hides the real entry condition)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: the modifier already rejects a zero amount and the body then returns early on the same condition, so the two guards disagree about whether a zero deposit reverts or silently succeeds. Precondition: the caller sets _bullMode to false so the plain transfer branch runs.
- Invariant to test: a guard on an entry path must have exactly one behaviour; concretely, `claimedReward[account]` must stay reconciled with `userWOMDeposited[account]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the caller sets _bullMode to false so the plain transfer branch runs, then assert `claimedReward[account]` and `userWOMDeposited[account]` end identical in both runs.
