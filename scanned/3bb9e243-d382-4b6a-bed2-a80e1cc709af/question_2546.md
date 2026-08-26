# Q2546: WombatStaking.harvest - safeApprove without reset on the MasterWombat stake path

## Question
In wombat/WombatStaking.sol, _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Can an unprivileged attacker reach this through `harvest(address _lpToken)` while smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, and drive `IMintableERC20(poolInfo.receiptToken).totalSupply()` out of agreement with `IMasterWombat(masterWombat) staked balance for poolInfo.pid` - breaking the invariant that staking into MasterWombat must not be blockable by leftover allowance - for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: safeApprove without reset on the MasterWombat stake path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Precondition: smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit.
- Invariant to test: staking into MasterWombat must not be blockable by leftover allowance; concretely, `IMintableERC20(poolInfo.receiptToken).totalSupply()` must stay reconciled with `IMasterWombat(masterWombat) staked balance for poolInfo.pid`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, have the attacker run `harvest(address _lpToken)`, then assert the victim's claimable value and the `IMintableERC20(poolInfo.receiptToken).totalSupply()` versus `IMasterWombat(masterWombat) staked balance for poolInfo.pid` relation are unchanged by the attacker's transaction.
