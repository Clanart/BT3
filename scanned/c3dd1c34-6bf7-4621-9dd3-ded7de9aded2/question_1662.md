# Q1662: mWomSV.lockFor - mWomSV has no forceUnLock so value can only leave through the full cooldown

## Question
Note that in wombat/mWomSV.sol, unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Can an attacker holding only tokens bought on market reach it via `lockFor(uint256 _amount, address _for)` under the attacker arrived through SmartWomConvert.convertFor with _mode == 2 and force `userUnlockings[user][i].amountInCoolDown` apart from `maxSlot`, breaking the invariant that every locked position must retain at least one reachable exit path under all reachable states for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: mWomSV has no forceUnLock so value can only leave through the full cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3
- Exploit idea: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Precondition: the attacker arrived through SmartWomConvert.convertFor with _mode == 2.
- Invariant to test: every locked position must retain at least one reachable exit path under all reachable states; concretely, `userUnlockings[user][i].amountInCoolDown` must stay reconciled with `maxSlot`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker arrived through SmartWomConvert.convertFor with _mode == 2, have the attacker run `lockFor(uint256 _amount, address _for)`, then assert the victim's claimable value and the `userUnlockings[user][i].amountInCoolDown` versus `maxSlot` relation are unchanged by the attacker's transaction.
