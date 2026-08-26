# Q3185: mWomSV.lockFor - mWomSV has no forceUnLock so value can only leave through the full cooldown

## Question
wombat/mWomSV.sol: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. With _for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3 under attacker control and the mWOM balance of the locker is exactly equal to totalAmount before the action, can an unprivileged caller sequence `lockFor(uint256 _amount, address _for)` so that `totalAmount` and `IERC20(mWOM).balanceOf(address(this))` no longer reconcile, violating the invariant that every locked position must retain at least one reachable exit path under all reachable states and realising Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: mWomSV has no forceUnLock so value can only leave through the full cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3
- Exploit idea: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Precondition: the mWOM balance of the locker is exactly equal to totalAmount before the action.
- Invariant to test: every locked position must retain at least one reachable exit path under all reachable states; concretely, `totalAmount` must stay reconciled with `IERC20(mWOM).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (_for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3) under the mWOM balance of the locker is exactly equal to totalAmount before the action, asserting on every row that every locked position must retain at least one reachable exit path under all reachable states.
