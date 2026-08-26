# Q2454: WombatStaking.convertWOM - safeApprove without reset on the veWOM path

## Question
In wombat/WombatStaking.sol, convertWOM() calls IERC20(wom).safeApprove(veWom, _amount) with no prior zeroing, so any allowance residue left by a veWOM mint that does not consume the full amount makes every later conversion revert. Can an unprivileged attacker reach this through `convertWOM(uint256 _amount)` while smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, and drive `womRewards measured by balance delta` out of agreement with `the amount queued into poolInfo.rewarder` - breaking the invariant that an approval on a hot path must be idempotent and must not be able to permanently disable conversion - for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: safeApprove without reset on the veWOM path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: convertWOM() calls IERC20(wom).safeApprove(veWom, _amount) with no prior zeroing, so any allowance residue left by a veWOM mint that does not consume the full amount makes every later conversion revert. Precondition: smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit.
- Invariant to test: an approval on a hot path must be idempotent and must not be able to permanently disable conversion; concretely, `womRewards measured by balance delta` must stay reconciled with `the amount queued into poolInfo.rewarder`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Invariant/fuzz run over `convertWOM(uint256 _amount)`: constrain the setup so that smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, fuzz the attacker inputs (_amount, with no upper bound and no relation to who supplied the WOM), and assert after every call that an approval on a hot path must be idempotent and must not be able to permanently disable conversion.
