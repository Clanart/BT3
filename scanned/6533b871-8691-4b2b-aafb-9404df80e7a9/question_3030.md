# Q3030: mWomSV.startUnlock - startUnlock has no bribe-manager guard unlike VLMGP

## Question
Consider wombat/mWomSV.sol, where VLMGP.startUnlock refuses to drop the locked balance below userTotalVotedInVlmgp, but mWomSV.startUnlock has no equivalent check against any consumer that priced a benefit off getUserTotalLocked. Assuming the attacker holds a second address so lockFor can be used across two accounts, can an unprivileged attacker turn this into a divergence between `getUserAmountInCoolDown(user)` and `totalAmountInCoolDown` via `startUnlock(uint256 _amountToCoolDown)`, breaking the invariant that any external consumer that grants value from getUserTotalLocked must be re-validated when that balance falls and producing High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: startUnlock has no bribe-manager guard unlike VLMGP)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: VLMGP.startUnlock refuses to drop the locked balance below userTotalVotedInVlmgp, but mWomSV.startUnlock has no equivalent check against any consumer that priced a benefit off getUserTotalLocked. Precondition: the attacker holds a second address so lockFor can be used across two accounts.
- Invariant to test: any external consumer that grants value from getUserTotalLocked must be re-validated when that balance falls; concretely, `getUserAmountInCoolDown(user)` must stay reconciled with `totalAmountInCoolDown`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker holds a second address so lockFor can be used across two accounts, call `startUnlock(uint256 _amountToCoolDown)`, and assert `getUserAmountInCoolDown(user)` equals `totalAmountInCoolDown` and that no account can withdraw more than it put in.
