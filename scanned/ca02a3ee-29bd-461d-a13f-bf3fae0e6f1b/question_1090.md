# Q1090: mWomSV.cancelUnlock - cancelUnlock is the only slot mutator without nonReentrant

## Question
In wombat/mWomSV.sol, lock, lockFor, startUnlock and unlock all carry nonReentrant while cancelUnlock() does not, so it remains callable from inside an external call that another path opened. Does `cancelUnlock(uint256 _slotIndex)` let an unprivileged caller exploit that under the attacker's slot matured one block ago, so that `getUserAmountInCoolDown(user)` diverges from `totalAmountInCoolDown`, the invariant that all slot-mutating functions must share a single reentrancy domain is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWomSV.sol -> `cancelUnlock(uint256 _slotIndex)` (mechanism: cancelUnlock is the only slot mutator without nonReentrant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `cancelUnlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the moment the cooldown is aborted
- Exploit idea: lock, lockFor, startUnlock and unlock all carry nonReentrant while cancelUnlock() does not, so it remains callable from inside an external call that another path opened. Precondition: the attacker's slot matured one block ago.
- Invariant to test: all slot-mutating functions must share a single reentrancy domain; concretely, `getUserAmountInCoolDown(user)` must stay reconciled with `totalAmountInCoolDown`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_slotIndex and the moment the cooldown is aborted) under the attacker's slot matured one block ago, asserting on every row that all slot-mutating functions must share a single reentrancy domain.
