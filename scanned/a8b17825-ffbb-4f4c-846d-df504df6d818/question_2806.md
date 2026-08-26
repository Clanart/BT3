# Q2806: mWomSV.cancelUnlock - cancelUnlock is the only slot mutator without nonReentrant

## Question
wombat/mWomSV.sol: lock, lockFor, startUnlock and unlock all carry nonReentrant while cancelUnlock() does not, so it remains callable from inside an external call that another path opened. With _slotIndex and the moment the cooldown is aborted under attacker control and a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder, can an unprivileged caller sequence `cancelUnlock(uint256 _slotIndex)` so that `mWomSV.getUserTotalLocked(user)` and `ArbWomUp3.calDoubledCounted(user)` no longer reconcile, violating the invariant that all slot-mutating functions must share a single reentrancy domain and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWomSV.sol -> `cancelUnlock(uint256 _slotIndex)` (mechanism: cancelUnlock is the only slot mutator without nonReentrant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `cancelUnlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the moment the cooldown is aborted
- Exploit idea: lock, lockFor, startUnlock and unlock all carry nonReentrant while cancelUnlock() does not, so it remains callable from inside an external call that another path opened. Precondition: a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder.
- Invariant to test: all slot-mutating functions must share a single reentrancy domain; concretely, `mWomSV.getUserTotalLocked(user)` must stay reconciled with `ArbWomUp3.calDoubledCounted(user)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `cancelUnlock(uint256 _slotIndex)` sequence atomically under a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder, asserting at the end that `mWomSV.getUserTotalLocked(user)` still equals `ArbWomUp3.calDoubledCounted(user)` and the PoC's balance delta is non-positive.
