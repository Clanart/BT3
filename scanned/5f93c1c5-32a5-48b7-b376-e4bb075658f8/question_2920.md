# Q2920: mWomSV.startUnlock - mWomSV has no forceUnLock so value can only leave through the full cooldown

## Question
wombat/mWomSV.sol: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. With _amountToCoolDown and the timestamps written into the slot under attacker control and the attacker holds a second address so lockFor can be used across two accounts, can an unprivileged caller sequence `startUnlock(uint256 _amountToCoolDown)` so that `totalAmount` and `IERC20(mWOM).balanceOf(address(this))` no longer reconcile, violating the invariant that every locked position must retain at least one reachable exit path under all reachable states and realising Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: mWomSV has no forceUnLock so value can only leave through the full cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Precondition: the attacker holds a second address so lockFor can be used across two accounts.
- Invariant to test: every locked position must retain at least one reachable exit path under all reachable states; concretely, `totalAmount` must stay reconciled with `IERC20(mWOM).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amountToCoolDown and the timestamps written into the slot) under the attacker holds a second address so lockFor can be used across two accounts, asserting on every row that every locked position must retain at least one reachable exit path under all reachable states.
