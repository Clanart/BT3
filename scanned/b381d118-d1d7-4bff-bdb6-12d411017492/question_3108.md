# Q3108: AnkrBNBPoolHelper.deposit - safeApprove without reset before depositFor into MasterMagpie

## Question
wombat/AnkrBNBPoolHelper.sol: _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. With _amount and _minimumLiquidity under attacker control and a residual stakingToken balance from an earlier rounding sits on the helper, can an unprivileged caller sequence `deposit(uint256 _amount, uint256 _minimumLiquidity)` so that `_minimumLiquidity supplied by the caller` and `the LP actually minted by the Wombat pool` no longer reconcile, violating the invariant that an approval on the deposit hot path must be idempotent and realising High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: safeApprove without reset before depositFor into MasterMagpie)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity
- Exploit idea: _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Precondition: a residual stakingToken balance from an earlier rounding sits on the helper.
- Invariant to test: an approval on the deposit hot path must be idempotent; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Invariant/fuzz run over `deposit(uint256 _amount, uint256 _minimumLiquidity)`: constrain the setup so that a residual stakingToken balance from an earlier rounding sits on the helper, fuzz the attacker inputs (_amount and _minimumLiquidity), and assert after every call that an approval on the deposit hot path must be idempotent.
