# Q2825: mWomSV.lock - mWomSV has no forceUnLock so value can only leave through the full cooldown

## Question
In wombat/mWomSV.sol, unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Can an unprivileged attacker reach this through `lock(uint256 _amount)` while the attacker holds a second address so lockFor can be used across two accounts, and drive `getUserTotalLocked(user)` out of agreement with `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked` - breaking the invariant that every locked position must retain at least one reachable exit path under all reachable states - for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `lock(uint256 _amount)` (mechanism: mWomSV has no forceUnLock so value can only leave through the full cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lock(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block in which the mWOM lock is credited
- Exploit idea: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Precondition: the attacker holds a second address so lockFor can be used across two accounts.
- Invariant to test: every locked position must retain at least one reachable exit path under all reachable states; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Invariant/fuzz run over `lock(uint256 _amount)`: constrain the setup so that the attacker holds a second address so lockFor can be used across two accounts, fuzz the attacker inputs (_amount and the block in which the mWOM lock is credited), and assert after every call that every locked position must retain at least one reachable exit path under all reachable states.
