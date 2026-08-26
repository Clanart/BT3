# Q3048: mWomSV.unlock - mWomSV has no forceUnLock so value can only leave through the full cooldown

## Question
Consider wombat/mWomSV.sol, where unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Assuming the attacker holds a second address so lockFor can be used across two accounts, can an unprivileged attacker turn this into a divergence between `getRewardablePercentWAD(user)` and `_calExpireForfeit in mWOMSVBaseRewarder` via `unlock(uint256 _slotIndex)`, breaking the invariant that every locked position must retain at least one reachable exit path under all reachable states and producing Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `unlock(uint256 _slotIndex)` (mechanism: mWomSV has no forceUnLock so value can only leave through the full cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the redemption timing
- Exploit idea: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Precondition: the attacker holds a second address so lockFor can be used across two accounts.
- Invariant to test: every locked position must retain at least one reachable exit path under all reachable states; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `_calExpireForfeit in mWOMSVBaseRewarder`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker holds a second address so lockFor can be used across two accounts, call `unlock(uint256 _slotIndex)`, and assert `getRewardablePercentWAD(user)` equals `_calExpireForfeit in mWOMSVBaseRewarder` and that no account can withdraw more than it put in.
