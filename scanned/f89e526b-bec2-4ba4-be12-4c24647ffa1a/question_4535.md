# Q4535: WombatStaking.deposit - safeApprove without reset on the MasterWombat stake path

## Question
Note that in wombat/WombatStaking.sol, _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Can an attacker holding only tokens bought on market reach it via `deposit(address,uint256,uint256,address,address) via a pool helper` under the deposit token for the pool is wBNB and the helper arrived through depositNative and force `isPoolFeeFree[_lpToken]` apart from `feeInfos.length`, breaking the invariant that staking into MasterWombat must not be blockable by leftover allowance for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: safeApprove without reset on the MasterWombat stake path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Precondition: the deposit token for the pool is wBNB and the helper arrived through depositNative.
- Invariant to test: staking into MasterWombat must not be blockable by leftover allowance; concretely, `isPoolFeeFree[_lpToken]` must stay reconciled with `feeInfos.length`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the deposit token for the pool is wBNB and the helper arrived through depositNative, then assert `isPoolFeeFree[_lpToken]` and `feeInfos.length` end identical in both runs.
