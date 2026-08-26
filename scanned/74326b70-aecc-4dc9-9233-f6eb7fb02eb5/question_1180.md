# Q1180: mWomSV.lockFor - mWomSV has no forceUnLock so value can only leave through the full cooldown

## Question
wombat/mWomSV.sol - unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Can an unprivileged attacker controlling _for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3, under the attacker reached maxSlot so slot reuse is forced, exploit this through `lockFor(uint256 _amount, address _for)` to break the reconciliation between `getRewardablePercentWAD(user)` and `_calExpireForfeit in mWOMSVBaseRewarder` and the invariant that every locked position must retain at least one reachable exit path under all reachable states, yielding Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: mWomSV has no forceUnLock so value can only leave through the full cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3
- Exploit idea: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Precondition: the attacker reached maxSlot so slot reuse is forced.
- Invariant to test: every locked position must retain at least one reachable exit path under all reachable states; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `_calExpireForfeit in mWOMSVBaseRewarder`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker reached maxSlot so slot reuse is forced, have the attacker run `lockFor(uint256 _amount, address _for)`, then assert the victim's claimable value and the `getRewardablePercentWAD(user)` versus `_calExpireForfeit in mWOMSVBaseRewarder` relation are unchanged by the attacker's transaction.
