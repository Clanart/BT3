# Q3457: mWomSV.lock - mWomSV has no forceUnLock so value can only leave through the full cooldown

## Question
In wombat/mWomSV.sol, unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Can an unprivileged attacker reach this through `lock(uint256 _amount)` while the attacker repeats cancelUnlock and startUnlock inside one transaction, and drive `totalAmount` out of agreement with `IERC20(mWOM).balanceOf(address(this))` - breaking the invariant that every locked position must retain at least one reachable exit path under all reachable states - for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `lock(uint256 _amount)` (mechanism: mWomSV has no forceUnLock so value can only leave through the full cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lock(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block in which the mWOM lock is credited
- Exploit idea: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Precondition: the attacker repeats cancelUnlock and startUnlock inside one transaction.
- Invariant to test: every locked position must retain at least one reachable exit path under all reachable states; concretely, `totalAmount` must stay reconciled with `IERC20(mWOM).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount and the block in which the mWOM lock is credited) under the attacker repeats cancelUnlock and startUnlock inside one transaction, asserting on every row that every locked position must retain at least one reachable exit path under all reachable states.
