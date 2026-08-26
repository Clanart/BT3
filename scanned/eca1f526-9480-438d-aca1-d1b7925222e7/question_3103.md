# Q3103: WombatStaking.harvest - safeApprove without reset on the MasterWombat stake path

## Question
In wombat/WombatStaking.sol, _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Does `harvest(address _lpToken)` let an unprivileged caller exploit that under the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, so that `totalAccumulated in mWOM` diverges from `veWom balance of WombatStaking`, the invariant that staking into MasterWombat must not be blockable by leftover allowance is broken, and the result is Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: safeApprove without reset on the MasterWombat stake path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Precondition: the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction.
- Invariant to test: staking into MasterWombat must not be blockable by leftover allowance; concretely, `totalAccumulated in mWOM` must stay reconciled with `veWom balance of WombatStaking`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, then assert `totalAccumulated in mWOM` and `veWom balance of WombatStaking` end identical in both runs.
