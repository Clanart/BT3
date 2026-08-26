# Q1000: WombatStaking.convertWOM - safeApprove without reset on the veWOM path

## Question
Consider wombat/WombatStaking.sol, where convertWOM() calls IERC20(wom).safeApprove(veWom, _amount) with no prior zeroing, so any allowance residue left by a veWOM mint that does not consume the full amount makes every later conversion revert. Assuming the contract is holding WOM collected as a protocol fee that has not yet been split, can an unprivileged attacker turn this into a divergence between `IERC20(wom).balanceOf(address(this))` and `totalConverted in mWOM` via `convertWOM(uint256 _amount)`, breaking the invariant that an approval on a hot path must be idempotent and must not be able to permanently disable conversion and producing Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: safeApprove without reset on the veWOM path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: convertWOM() calls IERC20(wom).safeApprove(veWom, _amount) with no prior zeroing, so any allowance residue left by a veWOM mint that does not consume the full amount makes every later conversion revert. Precondition: the contract is holding WOM collected as a protocol fee that has not yet been split.
- Invariant to test: an approval on a hot path must be idempotent and must not be able to permanently disable conversion; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted in mWOM`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Invariant/fuzz run over `convertWOM(uint256 _amount)`: constrain the setup so that the contract is holding WOM collected as a protocol fee that has not yet been split, fuzz the attacker inputs (_amount, with no upper bound and no relation to who supplied the WOM), and assert after every call that an approval on a hot path must be idempotent and must not be able to permanently disable conversion.
