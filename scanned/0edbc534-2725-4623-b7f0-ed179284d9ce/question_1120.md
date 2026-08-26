# Q1120: mWomSV.lock - mWomSV has no forceUnLock so value can only leave through the full cooldown

## Question
wombat/mWomSV.sol: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Under the attacker reached maxSlot so slot reuse is forced, is there an unprivileged sequence of `lock(uint256 _amount)` that leaves `totalAmount` unreconciled with `IERC20(mWOM).balanceOf(address(this))`, violates the invariant that every locked position must retain at least one reachable exit path under all reachable states, and delivers Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `lock(uint256 _amount)` (mechanism: mWomSV has no forceUnLock so value can only leave through the full cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lock(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block in which the mWOM lock is credited
- Exploit idea: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Precondition: the attacker reached maxSlot so slot reuse is forced.
- Invariant to test: every locked position must retain at least one reachable exit path under all reachable states; concretely, `totalAmount` must stay reconciled with `IERC20(mWOM).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker reached maxSlot so slot reuse is forced, call `lock(uint256 _amount)`, and assert `totalAmount` equals `IERC20(mWOM).balanceOf(address(this))` and that no account can withdraw more than it put in.
