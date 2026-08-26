# Q1456: mWomSV.unlock - mWomSV has no forceUnLock so value can only leave through the full cooldown

## Question
Consider wombat/mWomSV.sol, where unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Assuming the attacker reached maxSlot so slot reuse is forced, can an unprivileged attacker turn this into a divergence between `mWomSV.getUserTotalLocked(user)` and `ArbWomUp3.calDoubledCounted(user)` via `unlock(uint256 _slotIndex)`, breaking the invariant that every locked position must retain at least one reachable exit path under all reachable states and producing Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `unlock(uint256 _slotIndex)` (mechanism: mWomSV has no forceUnLock so value can only leave through the full cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the redemption timing
- Exploit idea: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Precondition: the attacker reached maxSlot so slot reuse is forced.
- Invariant to test: every locked position must retain at least one reachable exit path under all reachable states; concretely, `mWomSV.getUserTotalLocked(user)` must stay reconciled with `ArbWomUp3.calDoubledCounted(user)`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker reached maxSlot so slot reuse is forced, call `unlock(uint256 _slotIndex)`, and assert `mWomSV.getUserTotalLocked(user)` equals `ArbWomUp3.calDoubledCounted(user)` and that no account can withdraw more than it put in.
