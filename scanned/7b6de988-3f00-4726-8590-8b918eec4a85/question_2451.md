# Q2451: mWomSV.lock - mWomSV has no forceUnLock so value can only leave through the full cooldown

## Question
Consider wombat/mWomSV.sol, where unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Assuming a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder, can an unprivileged attacker turn this into a divergence between `mWomSV.getUserTotalLocked(user)` and `ArbWomUp3.calDoubledCounted(user)` via `lock(uint256 _amount)`, breaking the invariant that every locked position must retain at least one reachable exit path under all reachable states and producing Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `lock(uint256 _amount)` (mechanism: mWomSV has no forceUnLock so value can only leave through the full cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lock(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block in which the mWOM lock is credited
- Exploit idea: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Precondition: a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder.
- Invariant to test: every locked position must retain at least one reachable exit path under all reachable states; concretely, `mWomSV.getUserTotalLocked(user)` must stay reconciled with `ArbWomUp3.calDoubledCounted(user)`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder, have the attacker run `lock(uint256 _amount)`, then assert the victim's claimable value and the `mWomSV.getUserTotalLocked(user)` versus `ArbWomUp3.calDoubledCounted(user)` relation are unchanged by the attacker's transaction.
