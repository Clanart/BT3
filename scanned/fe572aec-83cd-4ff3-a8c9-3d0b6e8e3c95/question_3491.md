# Q3491: mWomSV.lockFor - mWomSV has no forceUnLock so value can only leave through the full cooldown

## Question
In wombat/mWomSV.sol, unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Starting from a state where the attacker repeats cancelUnlock and startUnlock inside one transaction, can an unprivileged EOA use `lockFor(uint256 _amount, address _for)` to leave `getRewardablePercentWAD(user)` inconsistent with `_calExpireForfeit in mWOMSVBaseRewarder`, violating the invariant that every locked position must retain at least one reachable exit path under all reachable states and extracting Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: mWomSV has no forceUnLock so value can only leave through the full cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3
- Exploit idea: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Precondition: the attacker repeats cancelUnlock and startUnlock inside one transaction.
- Invariant to test: every locked position must retain at least one reachable exit path under all reachable states; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `_calExpireForfeit in mWOMSVBaseRewarder`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker repeats cancelUnlock and startUnlock inside one transaction, have the attacker run `lockFor(uint256 _amount, address _for)`, then assert the victim's claimable value and the `getRewardablePercentWAD(user)` versus `_calExpireForfeit in mWOMSVBaseRewarder` relation are unchanged by the attacker's transaction.
