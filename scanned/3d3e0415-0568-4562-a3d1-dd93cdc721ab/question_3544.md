# Q3544: WombatStaking.convertWOM - veWOM lock commits pooled WOM for lockDays with no user opt-out

## Question
wombat/WombatStaking.sol: convertWOM() locks for lockDays with no per-depositor accounting, so an mWOM holder's underlying WOM sits inside a veWOM lock they never agreed to and cannot address individually. Under the pool is marked isPoolFeeFree so the fee loop is skipped entirely, is there an unprivileged sequence of `convertWOM(uint256 _amount)` that leaves `feeInfos[i].value` unreconciled with `totalFee`, violates the invariant that the backing of a liquid wrapper must remain redeemable under terms its holders accepted, and delivers Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: veWOM lock commits pooled WOM for lockDays with no user opt-out)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: convertWOM() locks for lockDays with no per-depositor accounting, so an mWOM holder's underlying WOM sits inside a veWOM lock they never agreed to and cannot address individually. Precondition: the pool is marked isPoolFeeFree so the fee loop is skipped entirely.
- Invariant to test: the backing of a liquid wrapper must remain redeemable under terms its holders accepted; concretely, `feeInfos[i].value` must stay reconciled with `totalFee`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the pool is marked isPoolFeeFree so the fee loop is skipped entirely, call `convertWOM(uint256 _amount)`, and assert `feeInfos[i].value` equals `totalFee` and that no account can withdraw more than it put in.
