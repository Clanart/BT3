# Q0225: WombatStaking.harvest - safeApprove without reset on the MasterWombat stake path

## Question
wombat/WombatStaking.sol: _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Under the contract is holding WOM that mWOM._convert has just transferred in but not yet locked, is there an unprivileged sequence of `harvest(address _lpToken)` that leaves `isPoolFeeFree[_lpToken]` unreconciled with `feeInfos.length`, violates the invariant that staking into MasterWombat must not be blockable by leftover allowance, and delivers Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: safeApprove without reset on the MasterWombat stake path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Precondition: the contract is holding WOM that mWOM._convert has just transferred in but not yet locked.
- Invariant to test: staking into MasterWombat must not be blockable by leftover allowance; concretely, `isPoolFeeFree[_lpToken]` must stay reconciled with `feeInfos.length`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish the contract is holding WOM that mWOM._convert has just transferred in but not yet locked, have the attacker run `harvest(address _lpToken)`, then assert the victim's claimable value and the `isPoolFeeFree[_lpToken]` versus `feeInfos.length` relation are unchanged by the attacker's transaction.
