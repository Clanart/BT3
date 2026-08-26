# Q5031: vlMGPBaseRewarder.getRewards - attacker-chosen reward-token list reaches getRewards

## Question
Note that in rewards/vlMGPBaseRewarder.sol, MasterMagpie._multiClaim forwards the caller's _rewardTokens[i] verbatim, so the attacker selects which tokens are settled and which are left accruing while the MGP-side rewardDebt is advanced regardless. Can an attacker holding only tokens bought on market reach it via `getRewards(address _account, address _receiver, address[] _rewardTokens)` under the attacker settles the same reward token through two separate multiclaimSpec calls in one block and force `_calExpireForfeit(account,_amount)` apart from `vlMGP.getRewardablePercentWAD(account)`, breaking the invariant that the set of tokens a claimer names must not change the total value they are entitled to for High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: attacker-chosen reward-token list reaches getRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: MasterMagpie._multiClaim forwards the caller's _rewardTokens[i] verbatim, so the attacker selects which tokens are settled and which are left accruing while the MGP-side rewardDebt is advanced regardless. Precondition: the attacker settles the same reward token through two separate multiclaimSpec calls in one block.
- Invariant to test: the set of tokens a claimer names must not change the total value they are entitled to; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `vlMGP.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker settles the same reward token through two separate multiclaimSpec calls in one block, snapshot `_calExpireForfeit(account,_amount)` and `vlMGP.getRewardablePercentWAD(account)`, run the attacker's `getRewards(address _account, address _receiver, address[] _rewardTokens)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
