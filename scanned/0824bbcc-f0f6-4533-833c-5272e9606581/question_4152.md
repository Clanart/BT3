# Q4152: WombatStaking.deposit - safeApprove without reset on the MasterWombat stake path

## Question
wombat/WombatStaking.sol: _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Under several feeInfos entries are active at once and the harvested amount is small, is there an unprivileged sequence of `deposit(address,uint256,uint256,address,address) via a pool helper` that leaves `womRewards measured by balance delta` unreconciled with `the amount queued into poolInfo.rewarder`, violates the invariant that staking into MasterWombat must not be blockable by leftover allowance, and delivers Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: safeApprove without reset on the MasterWombat stake path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: _stakeToWombatMaster() calls IERC20(_lpToken).safeApprove(masterWombat, _lpAmount) with no reset, so residue there disables every deposit and every harvest that stakes. Precondition: several feeInfos entries are active at once and the harvested amount is small.
- Invariant to test: staking into MasterWombat must not be blockable by leftover allowance; concretely, `womRewards measured by balance delta` must stay reconciled with `the amount queued into poolInfo.rewarder`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Single-transaction PoC contract executing the whole `deposit(address,uint256,uint256,address,address) via a pool helper` sequence atomically under several feeInfos entries are active at once and the harvested amount is small, asserting at the end that `womRewards measured by balance delta` still equals `the amount queued into poolInfo.rewarder` and the PoC's balance delta is non-positive.
