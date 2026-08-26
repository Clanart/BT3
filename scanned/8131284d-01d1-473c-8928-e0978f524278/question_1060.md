# Q1060: vlMGPBaseRewarder.getRewards - totalStaked and balanceOf drawn from unrelated sources

## Question
Consider rewards/vlMGPBaseRewarder.sol, where totalStaked() reads IERC20(vlMGP).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Assuming the account's slot matured recently so the percent has only just begun to decay, can an unprivileged attacker turn this into a divergence between `totalStaked()` and `IERC20(vlMGP).totalSupply()` via `getRewards(address _account, address _receiver, address[] _rewardTokens)`, breaking the invariant that sum of balanceOf over all accounts must equal totalStaked at all times and producing Critical - Protocol insolvency?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: totalStaked and balanceOf drawn from unrelated sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: totalStaked() reads IERC20(vlMGP).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Precondition: the account's slot matured recently so the percent has only just begun to decay.
- Invariant to test: sum of balanceOf over all accounts must equal totalStaked at all times; concretely, `totalStaked()` must stay reconciled with `IERC20(vlMGP).totalSupply()`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `getRewards(address _account, address _receiver, address[] _rewardTokens)`: constrain the setup so that the account's slot matured recently so the percent has only just begun to decay, fuzz the attacker inputs (the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor), and assert after every call that sum of balanceOf over all accounts must equal totalStaked at all times.
