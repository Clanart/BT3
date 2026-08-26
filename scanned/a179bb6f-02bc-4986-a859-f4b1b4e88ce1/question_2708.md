# Q2708: mWomSV.unlock - mWomSV has no forceUnLock so value can only leave through the full cooldown

## Question
Note that in wombat/mWomSV.sol, unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Can an attacker holding only tokens bought on market reach it via `unlock(uint256 _slotIndex)` under a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder and force `totalAmount` apart from `IERC20(mWOM).balanceOf(address(this))`, breaking the invariant that every locked position must retain at least one reachable exit path under all reachable states for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `unlock(uint256 _slotIndex)` (mechanism: mWomSV has no forceUnLock so value can only leave through the full cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the redemption timing
- Exploit idea: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Precondition: a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder.
- Invariant to test: every locked position must retain at least one reachable exit path under all reachable states; concretely, `totalAmount` must stay reconciled with `IERC20(mWOM).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder, then assert `totalAmount` and `IERC20(mWOM).balanceOf(address(this))` end identical in both runs.
