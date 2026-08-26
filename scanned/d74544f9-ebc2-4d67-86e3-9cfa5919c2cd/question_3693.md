# Q3693: AnkrBNBPoolHelper.depositLP - safeApprove without reset before depositFor into MasterMagpie

## Question
In wombat/AnkrBNBPoolHelper.sol, _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Starting from a state where the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, can an unprivileged EOA use `depositLP(uint256 _lpAmount)` to leave `IERC20(stakingToken).balanceOf(address(this)) delta` inconsistent with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`, violating the invariant that an approval on the deposit hot path must be idempotent and extracting High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `depositLP(uint256 _lpAmount)` (mechanism: safeApprove without reset before depositFor into MasterMagpie)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount
- Exploit idea: _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Precondition: the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes.
- Invariant to test: an approval on the deposit hot path must be idempotent; concretely, `IERC20(stakingToken).balanceOf(address(this)) delta` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Foundry fork test against the deployed pool: set up the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, snapshot `IERC20(stakingToken).balanceOf(address(this)) delta` and `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`, run the attacker's `depositLP(uint256 _lpAmount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
