# Q3033: WombatStaking.convertWOM - safeApprove without reset on the veWOM path

## Question
In wombat/WombatStaking.sol, convertWOM() calls IERC20(wom).safeApprove(veWom, _amount) with no prior zeroing, so any allowance residue left by a veWOM mint that does not consume the full amount makes every later conversion revert. Starting from a state where the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, can an unprivileged EOA use `convertWOM(uint256 _amount)` to leave `isPoolFeeFree[_lpToken]` inconsistent with `feeInfos.length`, violating the invariant that an approval on a hot path must be idempotent and must not be able to permanently disable conversion and extracting Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: safeApprove without reset on the veWOM path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: convertWOM() calls IERC20(wom).safeApprove(veWom, _amount) with no prior zeroing, so any allowance residue left by a veWOM mint that does not consume the full amount makes every later conversion revert. Precondition: the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction.
- Invariant to test: an approval on a hot path must be idempotent and must not be able to permanently disable conversion; concretely, `isPoolFeeFree[_lpToken]` must stay reconciled with `feeInfos.length`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, call `convertWOM(uint256 _amount)`, and assert `isPoolFeeFree[_lpToken]` equals `feeInfos.length` and that no account can withdraw more than it put in.
