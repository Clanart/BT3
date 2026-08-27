### Title
Donation-induced truncation of `_provisionReward` permanently freezes queued yield - (File: rewards/BaseRewardPoolV2.sol)

### Summary
`totalStaked()` in `BaseRewardPoolV2.sol` derives the reward-distribution denominator from the *raw* ERC20 balance of the `operator` (`IERC20(stakingToken).balanceOf(operator)`), while individual user shares (`balanceOf(_account)`) come from `IMasterMagpie(operator).stakingInfo(...)`, a separate internally-tracked accounting mapping [1](#0-0) . Because any unprivileged holder of `stakingToken` can transfer tokens directly to `operator` without going through the staking/deposit flow, they can inflate `totalStaked()` independently of `stakingInfo`, front-running a legitimate `queueNewRewards` call by the reward manager.

### Finding Description
`_provisionReward`, invoked from `queueNewRewards` (onlyManager) or `donateRewards` (public), computes the reward-per-token increment as:

```solidity
rewardInfo.rewardPerTokenStored =
    rewardInfo.rewardPerTokenStored +
    (_amountReward * 10**stakingTokenDecimals) /
    totalStaked();
``` [2](#0-1) 

`totalStaked()` reads the raw token balance of `operator`, not a value gated by the deposit/stake bookkeeping [3](#0-2) . This differs from `balanceOf(_account)`, which uses `IMasterMagpie(operator).stakingInfo(...)` internal accounting [4](#0-3) . An attacker who is not staked at all can simply `IERC20(stakingToken).transfer(operator, X)` in the block/mempool position immediately preceding the manager's `queueNewRewards(_amountReward, _rewardToken)` transaction, inflating `totalStaked()` without affecting any user's tracked share.

If `X` is large enough relative to `_amountReward * 10**stakingTokenDecimals`, the integer division truncates to `0`. Crucially, the code only defers reward accumulation into `queuedRewards` when `totalStaked() == 0` [5](#0-4) ; when `totalStaked() != 0` but the division still rounds to zero, the transferred `_amountReward` tokens are pulled into the contract (`historicalRewards` incremented, tokens held) but **no** `rewardPerTokenStored` credit and **no** `queuedRewards` fallback occurs — the tokens become permanently unclaimable by any staker, with no rescue mechanism in the contract.

`updateFor`/`_updateFor` themselves do not read `totalStaked()` at all — they only read the already-stored `rewardPerTokenStored` and settle a user's earned amount using the previously computed value [6](#0-5) . The prompt's framing of the exploit going through `updateFor` is imprecise: the actual vulnerable code path is `_provisionReward` (reached via `queueNewRewards`/`donateRewards`), not `updateFor`. `updateFor` plays no causal role in the truncation; it can only observe the already-corrupted `rewardPerTokenStored` afterward.

No modifier, `nonReentrant`, or check reconciles `totalStaked()` (raw balance) against the internally tracked staked amount, so the donation-based denominator inflation is unmitigated.

### Impact Explanation
If a manager's `queueNewRewards` transaction is front-run by a raw-token donation to `operator`, the entire freshly-queued reward amount can be rounded down to zero credit while the tokens remain locked in the contract with no code path to recover or re-queue them (the `queuedRewards` fallback only triggers on a literal `totalStaked() == 0`, not on division-to-zero). This matches "High – Permanent freezing of unclaimed yield," since legitimate stakers can never claim the value that was pulled from the reward manager.

### Likelihood Explanation
- Requires the staking token to be transferable directly to `operator` outside of the stake/deposit accounting path, which is true for any standard ERC20 token — no protocol privilege needed.
- Requires the attacker to see a pending `queueNewRewards`/`donateRewards` transaction in the mempool (public, predictable manager operation) and front-run it with a plain `transfer`.
- Capital requirement scales with how large `totalStaked()` needs to become relative to `_amountReward * 10**stakingTokenDecimals`; for low-decimal receipt tokens (small `10**stakingTokenDecimals`) even modest capital can drive the numerator below the existing/inflated denominator, per the question's precondition.
- Fully repeatable each time a distribution is queued, as `totalStaked()` is recomputed live from the token balance and is never reconciled against tracked stake.

### Recommendation
Track `totalStaked()` via the same internally-audited accounting used for `balanceOf(_account)` (e.g., sum of `stakingInfo` balances or an explicit `totalSupply`-style counter incremented/decremented only on `stake`/`withdraw`), rather than reading the raw `balanceOf(operator)`. Additionally, in `_provisionReward`, fall back to accumulating into `queuedRewards` whenever the computed increment rounds to `0` (not only when `totalStaked() == 0`), so distributed rewards are never silently dropped.

### Proof of Concept
Hardhat/Foundry test plan:
1. Deploy `BaseRewardPoolV2` with a low-decimal (e.g., 6-decimal) mock `stakingToken` and mock `MasterMagpie` (`operator`) exposing `stakingInfo`.
2. Have a legitimate user stake a small amount via `MasterMagpie` so `stakingInfo` records a nonzero share, and `balanceOf(operator)` reflects only that staked amount.
3. As an unprivileged attacker, call `stakingToken.transfer(operator, LARGE_AMOUNT)` directly (bypassing any deposit function) — assert `totalStaked()` (raw `balanceOf(operator)`) jumps to `staked + LARGE_AMOUNT` while `balanceOf(user)` via `stakingInfo` is unchanged.
4. In the same block, have the reward manager call `queueNewRewards(SMALL_REWARD_AMOUNT, rewardToken)`.
5. Assert `(SMALL_REWARD_AMOUNT * 10**stakingTokenDecimals) / totalStaked() == 0`, so `rewardPerTokenStored` for `rewardToken` is unchanged and `queuedRewards` is also unchanged (still 0), while `historicalRewards` increased by `SMALL_REWARD_AMOUNT` and the tokens sit in the contract.
6. Call `updateFor(user)` and `earned(user, rewardToken)` — assert both return `0` additional reward, confirming the queued `SMALL_REWARD_AMOUNT` is permanently unclaimable by any staker with no code path to reclaim it.

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

**File:** rewards/BaseRewardPoolV2.sol (L301-312)
```text
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
```

**File:** rewards/BaseRewardPoolV2.sol (L329-340)
```text
    function _updateFor(address _account) internal {
        uint256 length = rewardTokens.length;
        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            // if a reward stopped queuing, no need to recalculate to save gas fee
            if (userRewardPerTokenPaid[rewardToken][_account] == rewardPerToken(rewardToken))
                continue;

            userRewards[rewardToken][_account] = earned(_account, rewardToken);
            userRewardPerTokenPaid[rewardToken][_account] = rewardPerToken(rewardToken);
        }
    }
```
