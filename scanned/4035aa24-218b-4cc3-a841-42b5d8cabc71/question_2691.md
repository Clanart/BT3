# Q2691: WombatStaking.deposit - safeApprove without reset on the MasterWombat stake path

## Question
Consider wombat/WombatStaking.sol, where _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Assuming smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, can an unprivileged attacker turn this into a divergence between `totalAccumulated in mWOM` and `veWom balance of WombatStaking` via `deposit(address,uint256,uint256,address,address) via a pool helper`, breaking the invariant that staking into MasterWombat must not be blockable by leftover allowance and producing Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: safeApprove without reset on the MasterWombat stake path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Precondition: smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit.
- Invariant to test: staking into MasterWombat must not be blockable by leftover allowance; concretely, `totalAccumulated in mWOM` must stay reconciled with `veWom balance of WombatStaking`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, then assert `totalAccumulated in mWOM` and `veWom balance of WombatStaking` end identical in both runs.
