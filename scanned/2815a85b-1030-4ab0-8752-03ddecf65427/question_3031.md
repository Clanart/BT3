# Q3031: vlMGPBaseRewarder.getReward - totalStaked and balanceOf drawn from unrelated sources

## Question
Note that in rewards/vlMGPBaseRewarder.sol, totalStaked() reads IERC20(vlMGP).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Can an attacker holding only tokens bought on market reach it via `getReward(address _account, address _receiver)` under a large MGP distribution has just been queued and no account has settled yet and force `_calExpireForfeit(account,_amount)` apart from `vlMGP.getRewardablePercentWAD(account)`, breaking the invariant that sum of balanceOf over all accounts must equal totalStaked at all times for Critical - Protocol insolvency?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: totalStaked and balanceOf drawn from unrelated sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: totalStaked() reads IERC20(vlMGP).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: sum of balanceOf over all accounts must equal totalStaked at all times; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `vlMGP.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `getReward(address _account, address _receiver)`: constrain the setup so that a large MGP distribution has just been queued and no account has settled yet, fuzz the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path), and assert after every call that sum of balanceOf over all accounts must equal totalStaked at all times.
