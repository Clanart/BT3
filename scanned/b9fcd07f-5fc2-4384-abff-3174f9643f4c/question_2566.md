# Q2566: mWomSV.startUnlock - mWomSV has no forceUnLock so value can only leave through the full cooldown

## Question
wombat/mWomSV.sol: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Under a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder, is there an unprivileged sequence of `startUnlock(uint256 _amountToCoolDown)` that leaves `getUserAmountInCoolDown(user)` unreconciled with `totalAmountInCoolDown`, violates the invariant that every locked position must retain at least one reachable exit path under all reachable states, and delivers Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: mWomSV has no forceUnLock so value can only leave through the full cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Precondition: a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder.
- Invariant to test: every locked position must retain at least one reachable exit path under all reachable states; concretely, `getUserAmountInCoolDown(user)` must stay reconciled with `totalAmountInCoolDown`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder, call `startUnlock(uint256 _amountToCoolDown)`, and assert `getUserAmountInCoolDown(user)` equals `totalAmountInCoolDown` and that no account can withdraw more than it put in.
