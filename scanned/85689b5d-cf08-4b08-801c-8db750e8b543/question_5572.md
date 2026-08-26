# Q5572: WombatPoolHelper.depositLP - safeApprove without reset before depositFor into MasterMagpie

## Question
Consider wombat/WombatPoolHelper.sol, where _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Assuming the receipt token is minted to the helper while the credit is directed at a different address, can an unprivileged attacker turn this into a divergence between `pid cached at construction` and `pools[lpToken].pid in WombatStaking` via `depositLP(uint256 _lpAmount)`, breaking the invariant that an approval on the deposit hot path must be idempotent and producing High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositLP(uint256 _lpAmount)` (mechanism: safeApprove without reset before depositFor into MasterMagpie)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount and the LP tokens pulled from the caller
- Exploit idea: _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Precondition: the receipt token is minted to the helper while the credit is directed at a different address.
- Invariant to test: an approval on the deposit hot path must be idempotent; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Unit test with mocked Wombat and router legs: arrange the receipt token is minted to the helper while the credit is directed at a different address, call `depositLP(uint256 _lpAmount)`, and assert `pid cached at construction` equals `pools[lpToken].pid in WombatStaking` and that no account can withdraw more than it put in.
