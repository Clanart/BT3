# Q3169: vlMGPBaseRewarder.updateFor - totalStaked and balanceOf drawn from unrelated sources

## Question
Note that in rewards/vlMGPBaseRewarder.sol, totalStaked() reads IERC20(vlMGP).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Can an attacker holding only tokens bought on market reach it via `updateFor(address _account)` under the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them and force `rewards[_rewardToken].historicalRewards` apart from `IERC20(_rewardToken).balanceOf(address(this))`, breaking the invariant that sum of balanceOf over all accounts must equal totalStaked at all times for Critical - Protocol insolvency?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: totalStaked and balanceOf drawn from unrelated sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: totalStaked() reads IERC20(vlMGP).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Precondition: the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them.
- Invariant to test: sum of balanceOf over all accounts must equal totalStaked at all times; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, call `updateFor(address _account)`, and assert `rewards[_rewardToken].historicalRewards` equals `IERC20(_rewardToken).balanceOf(address(this))` and that no account can withdraw more than it put in.
