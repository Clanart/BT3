# Q3134: mWomSV.cancelUnlock - cancelUnlock is the only slot mutator without nonReentrant

## Question
wombat/mWomSV.sol: lock, lockFor, startUnlock and unlock all carry nonReentrant while cancelUnlock() does not, so it remains callable from inside an external call that another path opened. With _slotIndex and the moment the cooldown is aborted under attacker control and the attacker holds a second address so lockFor can be used across two accounts, can an unprivileged caller sequence `cancelUnlock(uint256 _slotIndex)` so that `getUserTotalLocked(user)` and `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked` no longer reconcile, violating the invariant that all slot-mutating functions must share a single reentrancy domain and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWomSV.sol -> `cancelUnlock(uint256 _slotIndex)` (mechanism: cancelUnlock is the only slot mutator without nonReentrant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `cancelUnlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the moment the cooldown is aborted
- Exploit idea: lock, lockFor, startUnlock and unlock all carry nonReentrant while cancelUnlock() does not, so it remains callable from inside an external call that another path opened. Precondition: the attacker holds a second address so lockFor can be used across two accounts.
- Invariant to test: all slot-mutating functions must share a single reentrancy domain; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `cancelUnlock(uint256 _slotIndex)` sequence atomically under the attacker holds a second address so lockFor can be used across two accounts, asserting at the end that `getUserTotalLocked(user)` still equals `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked` and the PoC's balance delta is non-positive.
