# Q3222: WombatStaking.deposit - safeApprove without reset on the MasterWombat stake path

## Question
In wombat/WombatStaking.sol, _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Does `deposit(address,uint256,uint256,address,address) via a pool helper` let an unprivileged caller exploit that under the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, so that `IERC20(wom).balanceOf(address(this))` diverges from `totalConverted in mWOM`, the invariant that staking into MasterWombat must not be blockable by leftover allowance is broken, and the result is Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: safeApprove without reset on the MasterWombat stake path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Precondition: the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction.
- Invariant to test: staking into MasterWombat must not be blockable by leftover allowance; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted in mWOM`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, then assert `IERC20(wom).balanceOf(address(this))` and `totalConverted in mWOM` end identical in both runs.
