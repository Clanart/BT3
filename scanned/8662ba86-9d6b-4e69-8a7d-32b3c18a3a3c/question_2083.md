# Q2083: mWomSV.lockFor - mWomSV has no forceUnLock so value can only leave through the full cooldown

## Question
In wombat/mWomSV.sol, unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Starting from a state where the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2, can an unprivileged EOA use `lockFor(uint256 _amount, address _for)` to leave `mWomSV.getUserTotalLocked(user)` inconsistent with `ArbWomUp3.calDoubledCounted(user)`, violating the invariant that every locked position must retain at least one reachable exit path under all reachable states and extracting Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: mWomSV has no forceUnLock so value can only leave through the full cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3
- Exploit idea: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Precondition: the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2.
- Invariant to test: every locked position must retain at least one reachable exit path under all reachable states; concretely, `mWomSV.getUserTotalLocked(user)` must stay reconciled with `ArbWomUp3.calDoubledCounted(user)`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Foundry fork test against the deployed pool: set up the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2, snapshot `mWomSV.getUserTotalLocked(user)` and `ArbWomUp3.calDoubledCounted(user)`, run the attacker's `lockFor(uint256 _amount, address _for)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
