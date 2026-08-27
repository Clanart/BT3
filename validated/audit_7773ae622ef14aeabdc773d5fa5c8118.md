### Title
`getRewards()` in `BaseRewardPool` is an empty stub, permanently silently failing to pay out claimed yield - (File: `rewards/BaseRewardPool.sol`)

### Summary
`BaseRewardPool.sol` implements the `IBaseRewardPool` interface's `getRewards(address _account, address _receiver, address[] memory _rewardTokens)` function as an empty function body that does nothing, while the singular `getReward()` and the equivalent `getRewards()` functions in the sibling reward-pool implementations (`BaseRewardPoolV2`, `vlMGPBaseRewarder`, `mWOMSVBaseRewarder`) are fully implemented with reward calculation and `safeTransfer` logic. This is directly analogous to the reported bug class: a documented/expected withdrawal function exists in name and interface but performs no actual dividend/reward payout.

### Finding Description
`BaseRewardPool.getReward()` correctly iterates `rewardTokens`, zeroes `userRewards`, and transfers the earned reward to `_receiver`: [1](#0-0) 

However, the multi-token variant `getRewards()`, which is meant to let a user claim a specific subset of reward tokens (the same role `getERC20TokenDividend()` was documented to play for Lien/baseToken dividends), is left completely empty: [2](#0-1) 

Compare this to the same function correctly implemented elsewhere in the codebase, which applies `updateRewards`, iterates the requested `_rewardTokens`, and calls `_sendReward` for each: [3](#0-2) [4](#0-3) 

Because the `BaseRewardPool.getRewards()` stub lacks the `updateReward`/`updateRewards` modifier and any transfer logic, calling it neither reverts nor pays out any tokens — it is a silent no-op. Any pool that is wired up to a plain `BaseRewardPool` (as opposed to `BaseRewardPoolV2`) and is reached through a code path in `MasterMagpie` that calls the per-token `getRewards` (e.g. `multiclaimSpec`/`multiclaimFor` with an explicit reward-token list, as opposed to `multiclaim` which defaults to empty lists) will consume gas and appear to succeed, but the user receives none of the specified bonus reward tokens.

Note: I was not able to fully confirm, due to index truncation, the exact branching logic inside `MasterMagpie._claimBaseRewarder`/`_multiClaim` that decides whether `getReward()` (all tokens) or `getRewards()` (specific tokens) is invoked for a given staking pool's rewarder. This determines whether affected users have an alternative full-claim path available or whether the specific-token claim is the only way to retrieve certain accrued bonus tokens for pools using the plain `BaseRewardPool`.

### Impact Explanation
If any staking pool's rewarder is deployed as `BaseRewardPool` (not the `V2` variant) and users interact with the specific-token claim entrypoint (`getRewards`), their accrued bonus/dividend rewards for that call are not transferred, while gas is spent and (depending on the exact `MasterMagpie` wiring) potentially without ever updating the user's reward-per-token checkpoint through this path. This matches "theft or permanent freezing of unclaimed yield" for the affected reward tokens/pools, since the intended withdrawal mechanism silently fails to deliver funds that are otherwise correctly accounted for and held by the contract.

### Likelihood Explanation
Likelihood depends entirely on whether any production pool uses `BaseRewardPool` (rather than `BaseRewardPoolV2`) as its rewarder, and whether the specific-token claim path (`getRewards`) is reachable by ordinary users via `MasterMagpie.multiclaimSpec`/`multiclaimFor`/`multiclaimOnBehalf` with a non-empty per-pool reward-token list, as suggested by: [5](#0-4) 

### Recommendation
Implement `BaseRewardPool.getRewards()` with the same logic pattern as `BaseRewardPoolV2.getRewards()` / `vlMGPBaseRewarder.getRewards()`: apply the `onlyMasterMagpie` and `updateRewards` modifiers, iterate `_rewardTokens`, and transfer each user's earned reward to `_receiver`, mirroring `getReward()`'s transfer logic but scoped to the requested token subset.

### Proof of Concept
1. Deploy a pool whose rewarder is `BaseRewardPool` (not `BaseRewardPoolV2`) with a bonus reward token queued via `queueNewRewards`.
2. A user stakes and accrues bonus reward via normal pool activity, verified via `earned(account, rewardToken)`.
3. User calls `MasterMagpie.multiclaimSpec([stakingToken], [[bonusRewardToken]])`, which (per the interface) routes to the pool rewarder's `getRewards(account, receiver, [bonusRewardToken])`.
4. Transaction succeeds (no revert), consumes gas, emits no `RewardPaid` event, and the user's ERC20 balance for `bonusRewardToken` does not increase — the empty function body at [2](#0-1) 
performs no transfer, unlike the working `getReward()` implementation at [1](#0-0) .

### Citations

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

**File:** rewards/BaseRewardPool.sol (L242-244)
```text
    function getRewards(address _account, address _receiver, address[] memory _rewardTokens) override external {

    }
```

**File:** rewards/BaseRewardPoolV2.sol (L237-250)
```text
    function getRewards(address _account, address _receiver, address[] memory _rewardTokens) override
        external
        onlyMasterMagpie
        updateRewards(_account, _rewardTokens)
    {
        uint256 length = _rewardTokens.length;
        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = _rewardTokens[index];
            uint256 reward = userRewards[rewardToken][_account]; // updated during updateReward modifier
            if (reward > 0) {
                _sendReward(rewardToken, _account, _receiver, reward);
            }
        }
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L248-260)
```text
    function getRewards(address _account, address _receiver, address[] memory _rewardTokens)
        public
        onlyMasterMagpie
        updateRewards(_account, _rewardTokens)
        nonReentrant
    {
        uint256 length = _rewardTokens.length;

        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = _rewardTokens[index];
            _sendReward(rewardToken, _account, _receiver);
        }
    }
```

**File:** rewards/MasterMagpie.sol (L406-417)
```text
    function multiclaimSpec(address[] calldata _stakingTokens, address[][] memory _rewardTokens)
        external whenNotPaused
    {
        _multiClaim(_stakingTokens, msg.sender, msg.sender, _rewardTokens);
    }

    /// @notice Claims for each of the pools with specified rewards to claim for each pool
    function multiclaimFor(address[] calldata _stakingTokens, address[][] memory _rewardTokens, address _account)
        external whenNotPaused
    {
        _multiClaim(_stakingTokens, _account, _account, _rewardTokens);
    }
```
