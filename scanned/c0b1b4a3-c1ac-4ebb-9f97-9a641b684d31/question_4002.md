# Q4002: WombatStaking.convertWOM - veWOM lock commits pooled WOM for lockDays with no user opt-out

## Question
wombat/WombatStaking.sol: convertWOM() locks for lockDays with no per-depositor accounting, so an mWOM holder's underlying WOM sits inside a veWOM lock they never agreed to and cannot address individually. With _amount, with no upper bound and no relation to who supplied the WOM under attacker control and several feeInfos entries are active at once and the harvested amount is small, can an unprivileged caller sequence `convertWOM(uint256 _amount)` so that `womRewards measured by balance delta` and `the amount queued into poolInfo.rewarder` no longer reconcile, violating the invariant that the backing of a liquid wrapper must remain redeemable under terms its holders accepted and realising Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: veWOM lock commits pooled WOM for lockDays with no user opt-out)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: convertWOM() locks for lockDays with no per-depositor accounting, so an mWOM holder's underlying WOM sits inside a veWOM lock they never agreed to and cannot address individually. Precondition: several feeInfos entries are active at once and the harvested amount is small.
- Invariant to test: the backing of a liquid wrapper must remain redeemable under terms its holders accepted; concretely, `womRewards measured by balance delta` must stay reconciled with `the amount queued into poolInfo.rewarder`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish several feeInfos entries are active at once and the harvested amount is small, have the attacker run `convertWOM(uint256 _amount)`, then assert the victim's claimable value and the `womRewards measured by balance delta` versus `the amount queued into poolInfo.rewarder` relation are unchanged by the attacker's transaction.
