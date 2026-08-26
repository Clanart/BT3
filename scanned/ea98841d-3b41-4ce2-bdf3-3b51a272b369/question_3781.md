# Q3781: mWomSV.lockFor - mWomSV has no forceUnLock so value can only leave through the full cooldown

## Question
In wombat/mWomSV.sol, unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Does `lockFor(uint256 _amount, address _for)` let an unprivileged caller exploit that under the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder, so that `userUnlockings[user][i].amountInCoolDown` diverges from `maxSlot`, the invariant that every locked position must retain at least one reachable exit path under all reachable states is broken, and the result is Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: mWomSV has no forceUnLock so value can only leave through the full cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3
- Exploit idea: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Precondition: the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder.
- Invariant to test: every locked position must retain at least one reachable exit path under all reachable states; concretely, `userUnlockings[user][i].amountInCoolDown` must stay reconciled with `maxSlot`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Invariant/fuzz run over `lockFor(uint256 _amount, address _for)`: constrain the setup so that the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder, fuzz the attacker inputs (_for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3), and assert after every call that every locked position must retain at least one reachable exit path under all reachable states.
