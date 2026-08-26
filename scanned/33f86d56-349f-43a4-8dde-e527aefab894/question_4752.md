# Q4752: WombatStaking.convertWOM - safeApprove without reset on the veWOM path

## Question
Consider wombat/WombatStaking.sol, where convertWOM() calls IERC20(wom).safeApprove(veWom, _amount) with no prior zeroing, so any allowance residue left by a veWOM mint that does not consume the full amount makes every later conversion revert. Assuming the attacker deposits and withdraws through the same helper inside one transaction, can an unprivileged attacker turn this into a divergence between `totalAccumulated in mWOM` and `veWom balance of WombatStaking` via `convertWOM(uint256 _amount)`, breaking the invariant that an approval on a hot path must be idempotent and must not be able to permanently disable conversion and producing Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: safeApprove without reset on the veWOM path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: convertWOM() calls IERC20(wom).safeApprove(veWom, _amount) with no prior zeroing, so any allowance residue left by a veWOM mint that does not consume the full amount makes every later conversion revert. Precondition: the attacker deposits and withdraws through the same helper inside one transaction.
- Invariant to test: an approval on a hot path must be idempotent and must not be able to permanently disable conversion; concretely, `totalAccumulated in mWOM` must stay reconciled with `veWom balance of WombatStaking`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Invariant/fuzz run over `convertWOM(uint256 _amount)`: constrain the setup so that the attacker deposits and withdraws through the same helper inside one transaction, fuzz the attacker inputs (_amount, with no upper bound and no relation to who supplied the WOM), and assert after every call that an approval on a hot path must be idempotent and must not be able to permanently disable conversion.
