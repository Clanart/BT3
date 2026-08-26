# Q3862: vlMGPBaseRewarder.getReward - totalStaked and balanceOf drawn from unrelated sources

## Question
In rewards/vlMGPBaseRewarder.sol, totalStaked() reads IERC20(vlMGP).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Does `getReward(address _account, address _receiver)` let an unprivileged caller exploit that under totalStaked is zero and queuedRewards holds a backlog, so that `totalStaked()` diverges from `IERC20(vlMGP).totalSupply()`, the invariant that sum of balanceOf over all accounts must equal totalStaked at all times is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: totalStaked and balanceOf drawn from unrelated sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: totalStaked() reads IERC20(vlMGP).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Precondition: totalStaked is zero and queuedRewards holds a backlog.
- Invariant to test: sum of balanceOf over all accounts must equal totalStaked at all times; concretely, `totalStaked()` must stay reconciled with `IERC20(vlMGP).totalSupply()`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `getReward(address _account, address _receiver)`: constrain the setup so that totalStaked is zero and queuedRewards holds a backlog, fuzz the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path), and assert after every call that sum of balanceOf over all accounts must equal totalStaked at all times.
