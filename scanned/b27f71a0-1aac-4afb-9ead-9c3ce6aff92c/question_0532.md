# Q0532: mWomSV.cancelUnlock - cancelUnlock is the only slot mutator without nonReentrant

## Question
wombat/mWomSV.sol: lock, lockFor, startUnlock and unlock all carry nonReentrant while cancelUnlock() does not, so it remains callable from inside an external call that another path opened. Under the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18, is there an unprivileged sequence of `cancelUnlock(uint256 _slotIndex)` that leaves `getUserTotalLocked(user)` unreconciled with `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked`, violates the invariant that all slot-mutating functions must share a single reentrancy domain, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWomSV.sol -> `cancelUnlock(uint256 _slotIndex)` (mechanism: cancelUnlock is the only slot mutator without nonReentrant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `cancelUnlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the moment the cooldown is aborted
- Exploit idea: lock, lockFor, startUnlock and unlock all carry nonReentrant while cancelUnlock() does not, so it remains callable from inside an external call that another path opened. Precondition: the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: all slot-mutating functions must share a single reentrancy domain; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18, snapshot `getUserTotalLocked(user)` and `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked`, run the attacker's `cancelUnlock(uint256 _slotIndex)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
