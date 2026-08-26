# Q5544: AnkrBNBPoolHelper.deposit - safeApprove without reset before depositFor into MasterMagpie

## Question
In wombat/AnkrBNBPoolHelper.sol, _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Does `deposit(uint256 _amount, uint256 _minimumLiquidity)` let an unprivileged caller exploit that under MasterMagpie is paused so depositFor reverts while the Wombat leg has already executed, so that `pid cached at construction` diverges from `pools[lpToken].pid in WombatStaking`, the invariant that an approval on the deposit hot path must be idempotent is broken, and the result is High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: safeApprove without reset before depositFor into MasterMagpie)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity
- Exploit idea: _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Precondition: MasterMagpie is paused so depositFor reverts while the Wombat leg has already executed.
- Invariant to test: an approval on the deposit hot path must be idempotent; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Invariant/fuzz run over `deposit(uint256 _amount, uint256 _minimumLiquidity)`: constrain the setup so that MasterMagpie is paused so depositFor reverts while the Wombat leg has already executed, fuzz the attacker inputs (_amount and _minimumLiquidity), and assert after every call that an approval on the deposit hot path must be idempotent.
