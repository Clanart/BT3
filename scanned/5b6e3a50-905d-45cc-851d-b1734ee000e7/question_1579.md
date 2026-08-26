# Q1579: ArbWomUp2.incentiveDeposit - a redundant zero guard hides the real entry condition

## Question
Note that in wombat/ArbWomUp2.sol, the modifier already rejects a zero amount and the body then returns early on the same condition, so the two guards disagree about whether a zero deposit reverts or silently succeeds. Can an attacker holding only tokens bought on market reach it via `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` under the router pair for the bull swap holds thin liquidity and force `claimedReward[account]` apart from `userWOMDeposited[account]`, breaking the invariant that a guard on an entry path must have exactly one behaviour for High - Theft of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: a redundant zero guard hides the real entry condition)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: the modifier already rejects a zero amount and the body then returns early on the same condition, so the two guards disagree about whether a zero deposit reverts or silently succeeds. Precondition: the router pair for the bull swap holds thin liquidity.
- Invariant to test: a guard on an entry path must have exactly one behaviour; concretely, `claimedReward[account]` must stay reconciled with `userWOMDeposited[account]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens) under the router pair for the bull swap holds thin liquidity, asserting on every row that a guard on an entry path must have exactly one behaviour.
