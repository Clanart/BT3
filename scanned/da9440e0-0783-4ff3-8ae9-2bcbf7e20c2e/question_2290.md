# Q2290: mWomSV.startUnlock - startUnlock has no bribe-manager guard unlike VLMGP

## Question
Note that in wombat/mWomSV.sol, VLMGP.startUnlock refuses to drop the locked balance below userTotalVotedInVlmgp, but mWomSV.startUnlock has no equivalent check against any consumer that priced a benefit off getUserTotalLocked. Can an attacker holding only tokens bought on market reach it via `startUnlock(uint256 _amountToCoolDown)` under the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2 and force `mWomSV.getUserTotalLocked(user)` apart from `ArbWomUp3.calDoubledCounted(user)`, breaking the invariant that any external consumer that grants value from getUserTotalLocked must be re-validated when that balance falls for High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: startUnlock has no bribe-manager guard unlike VLMGP)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: VLMGP.startUnlock refuses to drop the locked balance below userTotalVotedInVlmgp, but mWomSV.startUnlock has no equivalent check against any consumer that priced a benefit off getUserTotalLocked. Precondition: the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2.
- Invariant to test: any external consumer that grants value from getUserTotalLocked must be re-validated when that balance falls; concretely, `mWomSV.getUserTotalLocked(user)` must stay reconciled with `ArbWomUp3.calDoubledCounted(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2, call `startUnlock(uint256 _amountToCoolDown)`, and assert `mWomSV.getUserTotalLocked(user)` equals `ArbWomUp3.calDoubledCounted(user)` and that no account can withdraw more than it put in.
