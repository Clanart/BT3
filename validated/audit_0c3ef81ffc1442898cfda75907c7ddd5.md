### Title
Direct `stakingToken` transfer to MasterMagpie dilutes `rewardPerTokenStored` via `totalStaked()` - (rewards/BaseRewardPoolV2.sol)

### Summary
`totalStaked()` in `BaseRewardPoolV2.sol` returns `IERC20(stakingToken).balanceOf(operator)` rather than a sum of internally tracked user balances, while individual balances are tracked separately via `IMasterMagpie(operator).stakingInfo(stakingToken, _account)`. An attacker can permanently inflate the reward-distribution denominator by directly transferring `stakingToken` to the MasterMagpie operator without staking, diluting the `rewardPerTokenStored` accrual rate for every future `queueNewRewards`/`donateRewards` call.

### Finding Description
`totalStaked()` is defined as: [1](#0-0) 
while user shares are tracked completely independently through the MasterMagpie's internal `stakingInfo` mapping: [2](#0-1) 

These two accounting sources are decoupled: `totalStaked()` follows the raw ERC20 balance of the operator contract, while `balanceOf(_account)` follows an internal ledger that is only updated on `deposit`/`withdraw` calls routed through MasterMagpie. This means any plain `IERC20.transfer(operator, amount)` by an unprivileged attacker increases `totalStaked()` without crediting any account's `balanceOf`.

The reward math in `_provisionReward` divides the newly queued reward by `totalStaked()`: [3](#0-2) 

Because the denominator is inflated by the donated tokens, `rewardPerTokenStored` increases by less than it should relative to the real staked supply, permanently diluting the reward rate that legitimate stakers accrue on every subsequent reward queuing event, for as long as the donated tokens remain in the operator's balance (there is no mechanism in `BaseRewardPoolV2`/`MasterMagpie` shown here to detect or sweep such non-accounted donations back out).

### Impact Explanation
This is a "theft/permanent dilution of unclaimed yield" issue: real stakers' `earned()` values (computed from `rewardPerTokenStored`) are calculated using a legitimate `userShare` (their true `balanceOf`), but the growth of `rewardPerTokenStored` itself is permanently suppressed by the inflated `totalStaked()` denominator, so every staker receives a smaller share of every future reward injection than their real stake ratio should entitle them to. This dilution persists until the donated balance is somehow removed from the operator contract — no such path is present in these contracts, so the underpayment is effectively permanent for all reward epochs following the donation.

### Likelihood Explanation
- Requires only holding `stakingToken` and calling `IERC20.transfer` — fully unprivileged.
- No special timing or reentrancy required; a single transaction suffices.
- However, this is a self-funding griefing attack: the attacker permanently loses the donated tokens (there's no way for them to reclaim it), and the magnitude of dilution scales with the ratio of donated amount to real total staked amount. To meaningfully dilute a large pool, the attacker must donate a comparably large amount of capital, making it capital-intensive but always feasible for an attacker willing to sacrifice funds purely to grief other stakers.
- Repeatable at any time by any holder of the staking token, and cumulative (multiple donations further compound the dilution).

### Recommendation
Track `totalStaked()` as an internally incremented/decremented state variable updated exclusively during `stake`/`withdraw` calls from the MasterMagpie operator (mirroring the pattern already used in `BribeRewardPool.sol`'s `totalSupply`), rather than reading the raw ERC20 balance of the operator. This removes the ability of unrelated token transfers to affect reward-rate accounting.

### Proof of Concept
Foundry test plan:
1. Deploy `MasterMagpie`, `BaseRewardPoolV2` pool for a mock ERC20 `stakingToken`, and a mock reward token.
2. Have a legitimate staker deposit `X` `stakingToken` through MasterMagpie's normal deposit flow (increments internal `stakingInfo` and transfers tokens to operator).
3. Record `totalStaked()` (== `X`) and call `queueNewRewards(R, rewardToken)`; record `rewardPerTokenStored_A`.
4. Reset state (or run parallel scenario): before queueing rewards, have an attacker EOA call `stakingToken.transfer(operator, D)` directly (no deposit/stake call), inflating operator's balance to `X + D`.
5. Call `queueNewRewards(R, rewardToken)` again; record `rewardPerTokenStored_B`.
6. Assert `rewardPerTokenStored_B < rewardPerTokenStored_A` (specifically `rewardPerTokenStored_B ≈ R * 10**decimals / (X + D)` vs. `rewardPerTokenStored_A ≈ R * 10**decimals / X`), confirming the legitimate staker's `earned()` for the same reward event is permanently reduced despite an unchanged `balanceOf`. [4](#0-3) [5](#0-4)

### Citations

**File:** rewards/BaseRewardPoolV2.sol (L126-136)
```text
    function totalStaked() public override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
    }

    /// @notice Returns amount of staked tokens in master magpie by account
    /// @param _account Address account
    /// @return Returns amount of staked tokens by account
    function balanceOf(address _account) public override virtual view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(operator).stakingInfo(stakingToken, _account);
        return staked;
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L290-313)
```text
    function _provisionReward(uint256 _amountReward, address _rewardToken) internal {
        IERC20(_rewardToken).safeTransferFrom(
            msg.sender,
            address(this),
            _amountReward
        );
        Reward storage rewardInfo = rewards[_rewardToken];
        rewardInfo.historicalRewards =
            rewardInfo.historicalRewards +
            _amountReward;

        if (totalStaked() == 0) {
            rewardInfo.queuedRewards += _amountReward;
        } else {
            if (rewardInfo.queuedRewards > 0) {
                _amountReward += rewardInfo.queuedRewards;
                rewardInfo.queuedRewards = 0;
            }
            rewardInfo.rewardPerTokenStored =
                rewardInfo.rewardPerTokenStored +
                (_amountReward * 10**stakingTokenDecimals) /
                totalStaked();
        }
        emit RewardAdded(_amountReward, _rewardToken);
```
