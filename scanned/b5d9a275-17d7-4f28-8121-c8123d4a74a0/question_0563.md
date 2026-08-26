# Q0563: mWomSV.lock - mWomSV has no forceUnLock so value can only leave through the full cooldown

## Question
Consider wombat/mWomSV.sol, where unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Assuming the attacker's slot matured one block ago, can an unprivileged attacker turn this into a divergence between `getUserAmountInCoolDown(user)` and `totalAmountInCoolDown` via `lock(uint256 _amount)`, breaking the invariant that every locked position must retain at least one reachable exit path under all reachable states and producing Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `lock(uint256 _amount)` (mechanism: mWomSV has no forceUnLock so value can only leave through the full cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lock(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block in which the mWOM lock is credited
- Exploit idea: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Precondition: the attacker's slot matured one block ago.
- Invariant to test: every locked position must retain at least one reachable exit path under all reachable states; concretely, `getUserAmountInCoolDown(user)` must stay reconciled with `totalAmountInCoolDown`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker's slot matured one block ago, then assert `getUserAmountInCoolDown(user)` and `totalAmountInCoolDown` end identical in both runs.
