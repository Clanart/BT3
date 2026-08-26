# Q5453: WombatPoolHelperV2.depositFor - safeApprove without reset before depositFor into MasterMagpie

## Question
In wombat/WombatPoolHelperV2.sol, _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Starting from a state where the receipt token is minted to the helper while the credit is directed at a different address, can an unprivileged EOA use `depositFor(uint256 _amount, address _for)` to leave `pid cached at construction` inconsistent with `pools[lpToken].pid in WombatStaking`, violating the invariant that an approval on the deposit hot path must be idempotent and extracting High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: safeApprove without reset before depositFor into MasterMagpie)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any address) and _amount, with _minimumLiquidity hardcoded to zero
- Exploit idea: _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Precondition: the receipt token is minted to the helper while the credit is directed at a different address.
- Invariant to test: an approval on the deposit hot path must be idempotent; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Unit test with mocked Wombat and router legs: arrange the receipt token is minted to the helper while the credit is directed at a different address, call `depositFor(uint256 _amount, address _for)`, and assert `pid cached at construction` equals `pools[lpToken].pid in WombatStaking` and that no account can withdraw more than it put in.
