# Q3532: WombatPoolHelperV2.withdraw - deposit and withdraw both run the full harvest and fee path

## Question
wombat/WombatPoolHelperV2.sol: WombatStaking._toMasterWomAndSendReward is invoked on every deposit, depositLP and withdraw, so any revert inside the fee loop, the smart convert leg or a rewarder queue blocks principal movement for the whole pool. Under a residual stakingToken balance from an earlier rounding sits on the helper, is there an unprivileged sequence of `withdraw(uint256 _liquidity, uint256 _minAmount)` that leaves `_minimumLiquidity supplied by the caller` unreconciled with `the LP actually minted by the Wombat pool`, violates the invariant that principal deposits and withdrawals must not depend on an external price or an optional reward leg succeeding, and delivers High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: deposit and withdraw both run the full harvest and fee path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount
- Exploit idea: WombatStaking._toMasterWomAndSendReward is invoked on every deposit, depositLP and withdraw, so any revert inside the fee loop, the smart convert leg or a rewarder queue blocks principal movement for the whole pool. Precondition: a residual stakingToken balance from an earlier rounding sits on the helper.
- Invariant to test: principal deposits and withdrawals must not depend on an external price or an optional reward leg succeeding; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Unit test with mocked Wombat and router legs: arrange a residual stakingToken balance from an earlier rounding sits on the helper, call `withdraw(uint256 _liquidity, uint256 _minAmount)`, and assert `_minimumLiquidity supplied by the caller` equals `the LP actually minted by the Wombat pool` and that no account can withdraw more than it put in.
