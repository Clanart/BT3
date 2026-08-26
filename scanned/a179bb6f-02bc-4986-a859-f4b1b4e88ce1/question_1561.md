# Q1561: vlMGPBaseRewarder.updateFor - totalStaked and balanceOf drawn from unrelated sources

## Question
Note that in rewards/vlMGPBaseRewarder.sol, totalStaked() reads IERC20(vlMGP).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Can an attacker holding only tokens bought on market reach it via `updateFor(address _account)` under the computed forfeit lands just below the _amount / 1000 dust threshold and force `totalStaked()` apart from `IERC20(vlMGP).totalSupply()`, breaking the invariant that sum of balanceOf over all accounts must equal totalStaked at all times for Critical - Protocol insolvency?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: totalStaked and balanceOf drawn from unrelated sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: totalStaked() reads IERC20(vlMGP).totalSupply() while balanceOf() reads MasterMagpie's UserInfo.amount for the same account, so the numerator and the denominator of every reward computation come from two independently maintained ledgers. Precondition: the computed forfeit lands just below the _amount / 1000 dust threshold.
- Invariant to test: sum of balanceOf over all accounts must equal totalStaked at all times; concretely, `totalStaked()` must stay reconciled with `IERC20(vlMGP).totalSupply()`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the computed forfeit lands just below the _amount / 1000 dust threshold, snapshot `totalStaked()` and `IERC20(vlMGP).totalSupply()`, run the attacker's `updateFor(address _account)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
