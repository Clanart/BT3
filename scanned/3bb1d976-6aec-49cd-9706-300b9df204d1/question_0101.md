# Q0101: WombatStaking.convertWOM - safeApprove without reset on the veWOM path

## Question
wombat/WombatStaking.sol: convertWOM() calls IERC20(wom).safeApprove(veWom, _amount) with no prior zeroing, so any allowance residue left by a veWOM mint that does not consume the full amount makes every later conversion revert. Under the contract is holding WOM that mWOM._convert has just transferred in but not yet locked, is there an unprivileged sequence of `convertWOM(uint256 _amount)` that leaves `totalAccumulated in mWOM` unreconciled with `veWom balance of WombatStaking`, violates the invariant that an approval on a hot path must be idempotent and must not be able to permanently disable conversion, and delivers Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: safeApprove without reset on the veWOM path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: convertWOM() calls IERC20(wom).safeApprove(veWom, _amount) with no prior zeroing, so any allowance residue left by a veWOM mint that does not consume the full amount makes every later conversion revert. Precondition: the contract is holding WOM that mWOM._convert has just transferred in but not yet locked.
- Invariant to test: an approval on a hot path must be idempotent and must not be able to permanently disable conversion; concretely, `totalAccumulated in mWOM` must stay reconciled with `veWom balance of WombatStaking`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish the contract is holding WOM that mWOM._convert has just transferred in but not yet locked, have the attacker run `convertWOM(uint256 _amount)`, then assert the victim's claimable value and the `totalAccumulated in mWOM` versus `veWom balance of WombatStaking` relation are unchanged by the attacker's transaction.
