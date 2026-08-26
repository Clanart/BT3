# Q5591: MasterMagpie.multiclaimFor - forced vlMGP lock of a victim's default-pool rewards

## Question
rewards/MasterMagpie.sol: in _multiClaim() the defaultPoolAmount branch calls _sendVlMGPFor(), which locks the MGP into vlMGP for _user instead of transferring it, and because multiclaimFor is permissionless an attacker can force a victim's liquid MGP rewards into a cooldown-bound vlMGP position. Under the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp, is there an unprivileged sequence of `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` that leaves `totalAllocPoint` unreconciled with `tokenToPoolInfo[_stakingToken].allocPoint`, violates the invariant that a third party must not be able to convert another user's liquid reward entitlement into a time-locked, penalty-bearing position, and delivers High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` (mechanism: forced vlMGP lock of a victim's default-pool rewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _account (any victim), the staking-token list and the per-pool reward-token lists
- Exploit idea: in _multiClaim() the defaultPoolAmount branch calls _sendVlMGPFor(), which locks the MGP into vlMGP for _user instead of transferring it, and because multiclaimFor is permissionless an attacker can force a victim's liquid MGP rewards into a cooldown-bound vlMGP position. Precondition: the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp.
- Invariant to test: a third party must not be able to convert another user's liquid reward entitlement into a time-locked, penalty-bearing position; concretely, `totalAllocPoint` must stay reconciled with `tokenToPoolInfo[_stakingToken].allocPoint`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp, then assert `totalAllocPoint` and `tokenToPoolInfo[_stakingToken].allocPoint` end identical in both runs.
