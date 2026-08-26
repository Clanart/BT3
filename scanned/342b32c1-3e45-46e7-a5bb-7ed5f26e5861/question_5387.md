# Q5387: AnkrBNBPoolHelper.depositLP - safeApprove without reset before depositFor into MasterMagpie

## Question
wombat/AnkrBNBPoolHelper.sol: _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. With _lpAmount under attacker control and the receipt token is minted to the helper while the credit is directed at a different address, can an unprivileged caller sequence `depositLP(uint256 _lpAmount)` so that `pid cached at construction` and `pools[lpToken].pid in WombatStaking` no longer reconcile, violating the invariant that an approval on the deposit hot path must be idempotent and realising High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `depositLP(uint256 _lpAmount)` (mechanism: safeApprove without reset before depositFor into MasterMagpie)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount
- Exploit idea: _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Precondition: the receipt token is minted to the helper while the credit is directed at a different address.
- Invariant to test: an approval on the deposit hot path must be idempotent; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Two-account fork test (victim and attacker): establish the receipt token is minted to the helper while the credit is directed at a different address, have the attacker run `depositLP(uint256 _lpAmount)`, then assert the victim's claimable value and the `pid cached at construction` versus `pools[lpToken].pid in WombatStaking` relation are unchanged by the attacker's transaction.
