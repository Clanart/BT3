# Q4593: VLMGP.unlock - matured slot left unredeemed decays the rewardable percent for everyone

## Question
In VLMGP.sol, for a slot whose endTime has passed, getRewardablePercentWAD adds amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), a term that shrinks without bound, so a user who never calls unlock has their whole vesting entitlement forfeited to the pool. Can an unprivileged attacker reach this through `unlock(uint256 _slotIndex)` while the attacker splits one exit across several slots to compare aggregate penalty against a single-slot exit, and drive `maxSlot` out of agreement with `userUnlockings[user].length` - breaking the invariant that value must not be confiscated purely because a user delayed a redemption they were entitled to make - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: matured slot left unredeemed decays the rewardable percent for everyone)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: for a slot whose endTime has passed, getRewardablePercentWAD adds amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), a term that shrinks without bound, so a user who never calls unlock has their whole vesting entitlement forfeited to the pool. Precondition: the attacker splits one exit across several slots to compare aggregate penalty against a single-slot exit.
- Invariant to test: value must not be confiscated purely because a user delayed a redemption they were entitled to make; concretely, `maxSlot` must stay reconciled with `userUnlockings[user].length`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `unlock(uint256 _slotIndex)`: constrain the setup so that the attacker splits one exit across several slots to compare aggregate penalty against a single-slot exit, fuzz the attacker inputs (_slotIndex and how long after endTime the slot is redeemed), and assert after every call that value must not be confiscated purely because a user delayed a redemption they were entitled to make.
