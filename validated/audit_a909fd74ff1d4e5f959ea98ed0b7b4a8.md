### Title
Atomic multi-pool reward claiming with no per-token isolation causes DOS / freezing of unclaimed rewards - ([File: rewards/MasterMagpie.sol], [File: rewards/BaseRewardPool.sol])

### Summary
`MasterMagpie._multiClaim` lets a user claim rewards from an arbitrary number of staking-token pools in a single transaction, and `BaseRewardPool.getReward`/`getRewards` performs a plain `safeTransfer` for every registered reward token of a pool with no isolation or try/catch. Because none of these transfers are wrapped defensively, a single reverting transfer for one reward token in one pool reverts the entire multi-pool claim, exactly mirroring the "Transfer design prone to DOS" pattern described in the external report (batched settlement transfers that revert the whole user transaction on one failure).

### Finding Description
`multiclaim`/`multiclaimFor`/`multiclaimSpec` all funnel into `_multiClaim`, which iterates over an arbitrary list of `_stakingTokens` supplied by the caller and, for each one, calls `_claimBaseRewarder` → `rewarder.getReward(_account, _receiver)` (or `getRewards`). [1](#0-0) [2](#0-1) 

`BaseRewardPool.getReward` then loops over every registered `rewardTokens[index]` for that pool and unconditionally does `IERC20(rewardToken).safeTransfer(_receiver, reward)`, with no try/catch and no per-token failure isolation: [3](#0-2) 

If any single reward-token transfer in any single pool in the batch reverts (e.g., the receiving address becomes blacklisted by a reward token such as USDC/USDT, or the token’s transfer path otherwise fails), the entire `safeTransfer` call reverts, which bubbles up through `getReward` → `_claimBaseRewarder` → `_multiClaim`, reverting the whole transaction — including the MGP claim accounting (`unClaimedMgp` reset, `rewardDebt` updates) and the reward claims for every *other unrelated* pool in `_stakingTokens`, even though only one token in one pool was problematic. This is architecturally the same weakness flagged in the external report: settlement across multiple items is done atomically with no isolation, so one failing transfer DOSes the whole batch.

### Impact Explanation
Because `updateReward`/`_updateFor` accrues `userRewards[rewardToken][_account]` before the transfer attempt, and the transfer failure reverts the whole transaction (including the state updates), the user's accrued rewards for that pool remain trapped: rewards keep accruing correctly on-chain, but the user can never successfully call `getReward` for that pool (directly or via `multiclaim`) as long as the blocked token cannot be transferred to them, since the same reverting transfer will occur every time. Additionally, bundling that pool into any `multiclaim` call together with other pools causes the entire batch — and thus the yield due from the other unaffected pools — to also revert, freezing unrelated unclaimed rewards for that user. This matches "theft or permanent freezing of unclaimed yield."

### Likelihood Explanation
Reward tokens are added by managers via `queueNewRewards`, and many real reward tokens used in DeFi are centralized stablecoins (USDC/USDT) capable of blacklisting individual addresses. Any ordinary unprivileged user whose address is later blacklisted by one such token (for reasons entirely outside the protocol, e.g. compliance action against that address) would permanently lose the ability to call `getReward`/`multiclaim` for any pool that has that token registered as a reward, and would also be unable to batch-claim other pools together with it. No admin or attacker action within the protocol is required — an ordinary transfer failure condition on an ERC20 the user already holds exposure to is sufficient.

### Recommendation
Wrap each reward-token transfer in `BaseRewardPool.getReward`/`getRewards` in a try/catch (or use a low-level `call` and check success), and if a transfer fails, keep the accrued `userRewards[rewardToken][_account]` balance intact (do not zero it) instead of reverting, so that the rest of the reward tokens/pools in the batch can still settle successfully.

### Proof of Concept
1. A pool has two reward tokens, `MGP` and `USDC`, registered via `queueNewRewards`.
2. A user's rewards accrue over time in both tokens.
3. The user's address later becomes blacklisted by `USDC` (an external, unprivileged event outside the user's or protocol's control).
4. The user calls `multiclaim([poolA, poolB])` to claim from `poolA` (has USDC reward) and unrelated `poolB`.
5. `_multiClaim` reaches `_claimBaseRewarder(poolA, ...)` → `getReward` → `safeTransfer(USDC, user, reward)` reverts because the recipient is blacklisted.
6. The revert propagates through `_multiClaim`, reverting the entire transaction — the user cannot claim `MGP` from `poolA`, and cannot claim any reward from `poolB` either, despite `poolB` having nothing to do with the blocked token. [4](#0-3) [5](#0-4)

### Citations

**File:** rewards/MasterMagpie.sol (L426-432)
```text
    /// @notice Claim for all rewards for the pools
    function multiclaim(address[] calldata _stakingTokens)
        external whenNotPaused
    {
        address[][] memory rewardTokens = new address[][](_stakingTokens.length);
        _multiClaim(_stakingTokens, msg.sender, msg.sender, rewardTokens);
    }
```

**File:** rewards/MasterMagpie.sol (L536-562)
```text
    function _multiClaim(address[] calldata _stakingTokens, address _user, address _receiver, address[][] memory _rewardTokens) internal nonReentrant {
        uint256 length = _stakingTokens.length;
        if (length != _rewardTokens.length) revert LengthMismatch();

        uint256 vlMGPPoolAmount;
        uint256 mWOmPoolAmount;
        uint256 defaultPoolAmount;

        for (uint256 i = 0; i < length; ++i) {
            address _stakingToken = _stakingTokens[i];
            UserInfo storage user = userInfo[_stakingToken][_user];
            
            updatePool(_stakingToken);
            uint256 claimableMgp = _calNewMGP(_stakingToken, _user) + unClaimedMgp[_stakingToken][_user];

            if (_stakingToken == address(vlmgp)) {
                vlMGPPoolAmount += claimableMgp;
            } else if (MPGRewardPool[_stakingToken]) {
                mWOmPoolAmount += claimableMgp;
            } else {
                defaultPoolAmount += claimableMgp;
            }

            unClaimedMgp[_stakingToken][_user] = 0;
            user.rewardDebt = (user.amount * tokenToPoolInfo[_stakingToken].accMGPPerShare) / 1e12;
            _claimBaseRewarder(_stakingToken, _user, _receiver, _rewardTokens[i]);
        }
```

**File:** rewards/MasterMagpie.sol (L618-628)
```text
    /// @notice Harvest reward token in BaseRewarder for an account. NOTE: Baserewarder use user staking token balance as source to
    /// calculate reward token amount
    function _claimBaseRewarder(address _stakingToken, address _account, address _receiver, address[] memory _rewardTokens) internal {
        IBaseRewardPool rewarder = IBaseRewardPool(tokenToPoolInfo[_stakingToken].rewarder);
        if (address(rewarder) != address(0)) {
            if (_rewardTokens.length > 0)
                rewarder.getRewards(_account, _receiver, _rewardTokens);
            else
                // if not specifiying any reward token, just claim them all
                rewarder.getReward(_account, _receiver);
        }
```

**File:** rewards/BaseRewardPool.sol (L221-240)
```text
    function getReward(address _account, address _receiver)
        override
        public
        onlyMasterMagpie
        updateReward(_account)
        returns (bool)
    {
        uint256 length = rewardTokens.length;
        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            uint256 reward = userRewards[rewardToken][_account]; // updated during updateReward modifier
            if (reward > 0) {
                userRewards[rewardToken][_account] = 0;
                IERC20(rewardToken).safeTransfer(_receiver, reward);
                emit RewardPaid(_account, _receiver, reward, rewardToken);
            }
        }

        return true;
    }
```
