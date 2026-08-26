# Q3851: WombatPoolHelper.deposit - safeApprove without reset before depositFor into MasterMagpie

## Question
In wombat/WombatPoolHelper.sol, _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Can an unprivileged attacker reach this through `deposit(uint256 _amount, uint256 _minimumLiquidity)` while the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, and drive `pid cached at construction` out of agreement with `pools[lpToken].pid in WombatStaking` - breaking the invariant that an approval on the deposit hot path must be idempotent - for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: safeApprove without reset before depositFor into MasterMagpie)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity, forwarded verbatim into the Wombat pool
- Exploit idea: _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Precondition: the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes.
- Invariant to test: an approval on the deposit hot path must be idempotent; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Single-transaction PoC contract executing the whole `deposit(uint256 _amount, uint256 _minimumLiquidity)` sequence atomically under the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, asserting at the end that `pid cached at construction` still equals `pools[lpToken].pid in WombatStaking` and the PoC's balance delta is non-positive.
