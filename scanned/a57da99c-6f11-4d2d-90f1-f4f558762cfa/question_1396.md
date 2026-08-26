# Q1396: ArbWomUp2.incentiveDeposit - the payout uses a raw transfer whose result is ignored

## Question
In wombat/ArbWomUp2.sol, the non-bull branch calls IERC20(busd).transfer(msg.sender, rewardToSend) rather than safeTransfer, so a token that returns false leaves the claimed counter advanced with nothing delivered. Can an unprivileged attacker reach this through `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` while the caller crosses several tier boundaries in one deposit, and drive `claimedReward[account]` out of agreement with `userWOMDeposited[account]` - breaking the invariant that a token transfer on a payout path must be checked - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: the payout uses a raw transfer whose result is ignored)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: the non-bull branch calls IERC20(busd).transfer(msg.sender, rewardToSend) rather than safeTransfer, so a token that returns false leaves the claimed counter advanced with nothing delivered. Precondition: the caller crosses several tier boundaries in one deposit.
- Invariant to test: a token transfer on a payout path must be checked; concretely, `claimedReward[account]` must stay reconciled with `userWOMDeposited[account]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the caller crosses several tier boundaries in one deposit, have the attacker run `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`, then assert the victim's claimable value and the `claimedReward[account]` versus `userWOMDeposited[account]` relation are unchanged by the attacker's transaction.
