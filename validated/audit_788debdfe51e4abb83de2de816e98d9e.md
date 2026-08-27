### Title
Fee-on-transfer reward token combined with public `donateRewards()` allows reward-index inflation and theft of unclaimed yield - (File: rewards/BaseRewardPoolV2.sol)

### Summary
`donateRewards()` is unrestricted — any address can call it for any already-registered reward token — and it forwards straight into `_provisionReward()`, which increments `rewardInfo.historicalRewards` and `rewardInfo.rewardPerTokenStored` using the caller-supplied `_amountReward` parameter rather than the amount the pool actually received. If the registered reward token charges a transfer fee, the pool credits more reward-per-token than it actually holds, and a staker who triggers this can claim a disproportionate share of the pool's pre-existing (real) reward balance that belongs to other stakers.

### Finding Description
`donateRewards()` only checks `isRewardToken[_rewardToken]` and then calls the internal helper: [1](#0-0) 

`_provisionReward()` pulls tokens via `safeTransferFrom` but computes the accounting delta from the nominal `_amountReward`, not from the actual balance change of the contract: [2](#0-1) 

If `_rewardToken` charges a transfer fee, `IERC20(_rewardToken).safeTransferFrom` moves less than `_amountReward` into the pool, yet `rewardInfo.rewardPerTokenStored` is bumped as if the full `_amountReward` arrived: `rewardInfo.rewardPerTokenStored += (_amountReward * 10**stakingTokenDecimals) / totalStaked()`. Because `rewardPerToken()` simply returns this stored value [3](#0-2) , every staker's `earned()` calculation is now based on an inflated index that the contract's real token balance cannot fully back.

Since `donateRewards()` has no access control (unlike `queueNewRewards`, which is `onlyManager`), any staker can:
1. Acquire/hold a share of the staking pool (`balanceOf` mirrors `MasterMagpie.stakingInfo`, updated via ordinary `deposit`).
2. Call `donateRewards()` with a small `_amountReward` of a fee-on-transfer reward token, inflating `rewardPerTokenStored` by more than what was actually deposited.
3. Immediately harvest (via `MasterMagpie` → `getReward`/`getRewards`, gated only by `onlyMasterMagpie`, not by who funded the reward) to claim a share of the credited (but not fully backed) rewards proportional to their stake — draining part of the real balance that had previously accrued for other stakers.

This is a genuine accounting-integrity bug: `_provisionReward` never reconciles requested vs. actually-received tokens (no before/after balance check), and `donateRewards` exposes this path to any unprivileged caller at arbitrary block/amount, unlike the privileged `queueNewRewards` path.

Note: the specific invariant framing in the question (`rewardTokens.length` vs `isRewardToken` reconciliation) is not actually implicated — `donateRewards` never mutates `rewardTokens` or `isRewardToken`; only `queueNewRewards` does [4](#0-3) . The real, exploitable weakness is the missing received-amount reconciliation in `_provisionReward`, made attacker-reachable via the unrestricted `donateRewards`.

### Impact Explanation
An attacker holding a meaningful share of a pool's stake can use a fee-on-transfer registered reward token to inflate `rewardPerTokenStored` beyond what the pool actually holds, then harvest a disproportionate amount, siphoning previously accrued, unclaimed yield belonging to other stakers. This matches "High – Theft of unclaimed yield," but is strictly conditioned on a registered reward token that charges transfer fees, and on the attacker holding a large-enough stake share for the extracted excess to exceed the fee they paid.

### Likelihood Explanation
Exploitability depends entirely on whether any reward token registered for a given pool is fee-on-transfer/deflationary — this is not guaranteed and is a token-selection precondition outside the pool's control (typically standard tokens like WOM/MGP/stablecoins are used, none of which are fee-on-transfer in this protocol's known deployments). If such a token were ever registered, the attack is cheap and repeatable (any staker, any amount down to 1 wei, any block), but requires the attacker to already own or acquire a significant proportional stake to profit net of the fee they pay.

### Recommendation
In `_provisionReward`, measure the actual amount received (`balanceBefore`/`balanceAfter` delta on `_rewardToken`) and use that delta — not the caller-supplied `_amountReward` — for all `historicalRewards`/`rewardPerTokenStored`/`queuedRewards` accounting in both `donateRewards` and `queueNewRewards`.

### Proof of Concept
Foundry test plan:
1. Deploy `BaseRewardPoolV2` with a mock fee-on-transfer ERC20 as the registered reward token (e.g., 50% transfer fee) and a `MasterMagpie` instance.
2. Attacker (and a "victim" staker) both deposit staking tokens via `MasterMagpie.deposit`, attacker holding ≥60% of total stake.
3. Record `rewards[token].rewardPerTokenStored`, pool's actual token balance, and attacker's/victim's `earned()`.
4. Attacker calls `donateRewards(amount, feeToken)`; assert pool receives only `amount * (1-fee)` but `rewardPerTokenStored` increases based on full `amount`.
5. Attacker calls `MasterMagpie.withdraw`/harvest to trigger `getReward`; assert attacker receives more `feeToken` than they net-contributed (i.e., `received > amount*fee`), and that victim's `earned()` for that token can no longer be fully paid out (pool balance insufficient), demonstrating theft of the victim's previously accrued yield.

### Citations

**File:** rewards/BaseRewardPoolV2.sol (L145-152)
```text
    function rewardPerToken(address _rewardToken)
        public
        override
        view
        returns (uint256)
    {
        return rewards[_rewardToken].rewardPerTokenStored;
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L252-260)
```text
    /// @notice Sends new rewards to be distributed to the users staking. Only possible to donate already registered token
    /// @param _amountReward Amount of reward token to be distributed
    /// @param _rewardToken Address reward token
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L273-286)
```text
    function queueNewRewards(uint256 _amountReward, address _rewardToken)
        override
        external
        onlyManager
        returns (bool)
    {
        if (!isRewardToken[_rewardToken]) {
            rewardTokens.push(_rewardToken);
            isRewardToken[_rewardToken] = true;
        }

        _provisionReward(_amountReward, _rewardToken);
        return true;
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
