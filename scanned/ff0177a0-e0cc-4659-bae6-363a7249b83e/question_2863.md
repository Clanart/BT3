# Q2863: mWomSV.lockFor - mWomSV has no forceUnLock so value can only leave through the full cooldown

## Question
wombat/mWomSV.sol: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Under the attacker holds a second address so lockFor can be used across two accounts, is there an unprivileged sequence of `lockFor(uint256 _amount, address _for)` that leaves `getUserAmountInCoolDown(user)` unreconciled with `totalAmountInCoolDown`, violates the invariant that every locked position must retain at least one reachable exit path under all reachable states, and delivers Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: mWomSV has no forceUnLock so value can only leave through the full cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3
- Exploit idea: unlike VLMGP, wombat/mWomSV.sol exposes no forceUnLock path, so a slot whose accounting becomes inconsistent has no penalty-bearing escape hatch and the mWOM behind it cannot be recovered. Precondition: the attacker holds a second address so lockFor can be used across two accounts.
- Invariant to test: every locked position must retain at least one reachable exit path under all reachable states; concretely, `getUserAmountInCoolDown(user)` must stay reconciled with `totalAmountInCoolDown`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker holds a second address so lockFor can be used across two accounts, have the attacker run `lockFor(uint256 _amount, address _for)`, then assert the victim's claimable value and the `getUserAmountInCoolDown(user)` versus `totalAmountInCoolDown` relation are unchanged by the attacker's transaction.
