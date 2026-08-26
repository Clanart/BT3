# Q5481: WombatStaking.convertWOM - veWOM lock commits pooled WOM for lockDays with no user opt-out

## Question
In wombat/WombatStaking.sol, convertWOM() locks for lockDays with no per-depositor accounting, so an mWOM holder's underlying WOM sits inside a veWOM lock they never agreed to and cannot address individually. Can an unprivileged attacker reach this through `convertWOM(uint256 _amount)` while the veWOM contract leaves a non-zero allowance after mint, and drive `totalAccumulated in mWOM` out of agreement with `veWom balance of WombatStaking` - breaking the invariant that the backing of a liquid wrapper must remain redeemable under terms its holders accepted - for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: veWOM lock commits pooled WOM for lockDays with no user opt-out)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: convertWOM() locks for lockDays with no per-depositor accounting, so an mWOM holder's underlying WOM sits inside a veWOM lock they never agreed to and cannot address individually. Precondition: the veWOM contract leaves a non-zero allowance after mint.
- Invariant to test: the backing of a liquid wrapper must remain redeemable under terms its holders accepted; concretely, `totalAccumulated in mWOM` must stay reconciled with `veWom balance of WombatStaking`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish the veWOM contract leaves a non-zero allowance after mint, have the attacker run `convertWOM(uint256 _amount)`, then assert the victim's claimable value and the `totalAccumulated in mWOM` versus `veWom balance of WombatStaking` relation are unchanged by the attacker's transaction.
