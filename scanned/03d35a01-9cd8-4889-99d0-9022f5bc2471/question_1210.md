# Q1210: vlMGPBaseRewarder.getRewards - queueMGP(uint256,address,address) is the MGP entry point used by MasterMagpie._sendMGPForVlMGPPool

## Question
rewards/vlMGPBaseRewarder.sol: this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Under the account's slot matured recently so the percent has only just begun to decay, is there an unprivileged sequence of `getRewards(address _account, address _receiver, address[] _rewardTokens)` that leaves `balanceOf(account)` unreconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`, violates the invariant that only an authorised manager may decide when and by how much the reward index moves, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: queueMGP(uint256,address,address) is the MGP entry point used by MasterMagpie._sendMGPForVlMGPPool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Precondition: the account's slot matured recently so the percent has only just begun to decay.
- Invariant to test: only an authorised manager may decide when and by how much the reward index moves; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the account's slot matured recently so the percent has only just begun to decay, have the attacker run `getRewards(address _account, address _receiver, address[] _rewardTokens)`, then assert the victim's claimable value and the `balanceOf(account)` versus `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked` relation are unchanged by the attacker's transaction.
