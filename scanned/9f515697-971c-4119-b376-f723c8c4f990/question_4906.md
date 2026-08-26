# Q4906: AnkrBNBPoolHelper.depositLP - safeApprove without reset before depositFor into MasterMagpie

## Question
In wombat/AnkrBNBPoolHelper.sol, _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Can an unprivileged attacker reach this through `depositLP(uint256 _lpAmount)` while the attacker has moved the wom/mWom Wombat pool immediately before calling, and drive `IERC20(stakingToken).totalSupply()` out of agreement with `the MasterWombat staked balance for pid` - breaking the invariant that an approval on the deposit hot path must be idempotent - for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `depositLP(uint256 _lpAmount)` (mechanism: safeApprove without reset before depositFor into MasterMagpie)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount
- Exploit idea: _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Precondition: the attacker has moved the wom/mWom Wombat pool immediately before calling.
- Invariant to test: an approval on the deposit hot path must be idempotent; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Two-account fork test (victim and attacker): establish the attacker has moved the wom/mWom Wombat pool immediately before calling, have the attacker run `depositLP(uint256 _lpAmount)`, then assert the victim's claimable value and the `IERC20(stakingToken).totalSupply()` versus `the MasterWombat staked balance for pid` relation are unchanged by the attacker's transaction.
