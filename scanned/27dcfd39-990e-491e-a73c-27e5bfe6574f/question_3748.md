# Q3748: VLMGP.cancelUnlock - cancelUnlock is not nonReentrant while every sibling is

## Question
VLMGP.sol - lock, lockFor, startUnlock, unlock and forceUnLock all carry nonReentrant but cancelUnlock() does not, so it is the one slot-mutating path reachable from inside another external call. Can an unprivileged attacker controlling _slotIndex and the moment the cooldown is aborted, under the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, exploit this through `cancelUnlock(uint256 _slotIndex)` to break the reconciliation between `userTotalVotedInVlmgp(user) in WombatBribeManager` and `getUserTotalLocked(user)` and the invariant that all functions mutating userUnlockings and totalAmountInCoolDown must share one reentrancy domain, yielding Critical - Direct theft of user funds?

## Target
- File/function: VLMGP.sol -> `cancelUnlock(uint256 _slotIndex)` (mechanism: cancelUnlock is not nonReentrant while every sibling is)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `cancelUnlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the moment the cooldown is aborted
- Exploit idea: lock, lockFor, startUnlock, unlock and forceUnLock all carry nonReentrant but cancelUnlock() does not, so it is the one slot-mutating path reachable from inside another external call. Precondition: the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor.
- Invariant to test: all functions mutating userUnlockings and totalAmountInCoolDown must share one reentrancy domain; concretely, `userTotalVotedInVlmgp(user) in WombatBribeManager` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, snapshot `userTotalVotedInVlmgp(user) in WombatBribeManager` and `getUserTotalLocked(user)`, run the attacker's `cancelUnlock(uint256 _slotIndex)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
