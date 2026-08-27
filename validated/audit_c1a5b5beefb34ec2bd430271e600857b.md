### Title
Permissionless `donateRewards` combined with unchecked division by `totalStaked()` allows an attacker to overflow `rewardPerTokenStored`, permanently bricking reward accounting and freezing unclaimed yield - ([File: rewards/BaseRewardPool.sol])

### Summary
The rippled fix (`10555fa`) addresses an unhandled overflow exception thrown while summing `gateway_balances` obligations, converting it into a bounded "max valid amount" instead of crashing the read path. The analogous bug class here is **unbounded accumulator arithmetic that can be pushed to the point of unconditional revert**, but in this codebase the accumulator (`Reward.rewardPerTokenStored`) is not read-only telemetry — it is load-bearing state used on every reward claim, and Solidity 0.8's checked arithmetic means an attacker-induced overflow does not just fail gracefully, it permanently reverts the function for every future caller. [1](#0-0) 

### Finding Description
`donateRewards` is externally callable by **anyone**, with the only guard being that the token must already be registered as a reward token — there is no manager/owner check: [2](#0-1) 

It flows into `_provisionReward`, which does:
```
rewardInfo.rewardPerTokenStored =
    rewardInfo.rewardPerTokenStored +
    (_amountReward * 10**stakingDecimals()) /
    this.totalStaked();
``` [3](#0-2) 

`totalStaked()` returns the staking token's balance held by the `operator` (MasterMagpie) for that specific pool — a value directly controllable/observable by any caller for freshly created or thinly staked pools: [4](#0-3) 

Because the update multiplies by `10**stakingDecimals()` and divides by the *current* total staked amount, an attacker can drive `rewardPerTokenStored` toward `type(uint256).max` by repeatedly calling `donateRewards` while `totalStaked()` is small (e.g., right after pool creation, or any low-TVL/long-tail pool). Once `rewardPerTokenStored` is sufficiently large:

1. Any further legitimate `queueNewRewards`/`donateRewards` addition in `_provisionReward` (line 314-318) reverts with an arithmetic-overflow panic under Solidity 0.8's checked math, permanently blocking new reward distribution for that token.
2. `earned()` computes `balanceOf(_account) * (rewardPerToken(_rewardToken) - userRewardPerTokenPaid[...])` — with `rewardPerTokenStored` near `type(uint256).max` and any user whose `userRewardPerTokenPaid` is still low (e.g., new stakers, or the modifier being invoked for the first time), this multiplication overflows and reverts. [5](#0-4) 
3. `getReward()` is gated by the `updateReward(_account)` modifier, which loops over **all** registered reward tokens and calls `earned()`/`_updateFor` for each. A single poisoned reward token's overflow causes the entire loop, and therefore the whole `getReward()` call, to revert — even for unrelated, otherwise-claimable reward tokens for that user. [6](#0-5) [7](#0-6) 

The identical unguarded pattern (permissionless `donateRewards` + division by `totalStaked()`) exists in the sibling reward pools `vlMGPBaseRewarder.sol` and `mWOMSVBaseRewarder.sol`, and a structurally identical version keyed off `totalSupply` exists in `DelegateVoteRewardPool.sol`. [8](#0-7) [9](#0-8) [10](#0-9) 

### Impact Explanation
Once the accumulator is poisoned, users whose `earned()` computation triggers overflow can no longer claim already-accrued (and future) rewards for that token from that pool via `getReward()`, and the pool operator can no longer queue further reward distributions for the affected token. This constitutes permanent freezing of unclaimed yield for legitimate stakers — an outcome explicitly in scope. The exact reachability of this to *staked principal* withdrawal (via MasterMagpie's `_claimBaseRewarder`/`multiClaim` paths calling into `getReward`) was not fully confirmed within available context and should be verified further, but the freezing of unclaimed rewards for the affected reward token is directly demonstrable from the code shown above.

### Likelihood Explanation
`donateRewards` requires no privileged role and only requires the caller to actually transfer the `_amountReward` tokens in via `safeTransferFrom` — feasible for any ERC20 the attacker can acquire cheaply, especially against newly-created or low-TVL pools where `totalStaked()` is small, making the scaling division amplify a modest donation into an enormous `rewardPerTokenStored` increment. Reaching full `uint256` overflow may require several iterations/donations but is achievable well within normal transaction gas/cost budgets for low-decimal or cheap reward tokens.

### Recommendation
- Restrict `donateRewards` to `onlyManager` (or add a cap/rate-limit), removing the fully permissionless donation path.
- Guard the `rewardPerTokenStored` update and `earned()` multiplication against overflow, e.g., using `try/catch` or explicit bounds checks so a poisoned value is capped rather than permanently reverting the whole claim path (directly mirroring the referenced rippled fix's approach of clamping to a maximum valid value instead of throwing).
- Consider requiring a minimum `totalStaked()` before allowing `_provisionReward` to scale rewards, to prevent the low-TVL donation amplification.

### Proof of Concept
1. Attacker (or anyone) creates/uses a fresh MasterMagpie pool for staking token `S` with reward token `R`, where `totalStaked()` (i.e., `S.balanceOf(MasterMagpie)`) is very small (e.g., 1 wei, from being the first/only depositor).
2. Attacker calls `donateRewards(largeAmount, R)` on the pool's `BaseRewardPool`. Because `totalStaked()` is tiny, `(_amountReward * 10**stakingDecimals()) / totalStaked()` yields a disproportionately huge increment to `rewardPerTokenStored`. [3](#0-2) 
3. Repeating this (or using one large enough donation) pushes `rewardPerTokenStored` close to `type(uint256).max`.
4. Any subsequent call to `queueNewRewards`/`donateRewards` for `R` on this pool reverts (checked-math overflow) — reward distribution for `R` in this pool is permanently broken. [1](#0-0) 
5. Any user with nonzero `balanceOf(_account)` and `userRewardPerTokenPaid[R][account]` still below the poisoned `rewardPerTokenStored` triggers an overflow when `getReward()` computes `earned()`, reverting their entire claim transaction for all reward tokens in that pool. [11](#0-10)

### Citations

**File:** rewards/BaseRewardPool.sol (L124-128)
```text
    /// @notice Returns current amount of staked tokens
    /// @return Returns current amount of staked tokens
    function totalStaked() external override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
    }
```

**File:** rewards/BaseRewardPool.sol (L173-240)
```text
    function earned(address _account, address _rewardToken)
        public
        override
        view
        returns (uint256)
    {
        return (
            (((balanceOf(_account) *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                (10**stakingDecimals())) + userRewards[_rewardToken][_account])
        );
    }

    /// @notice Returns amount of all reward tokens
    /// @param _account Address account
    /// @return pendingBonusRewards as amounts of all rewards.
    function allEarned(address _account)
        external
        override
        view
        returns (
            uint256[] memory pendingBonusRewards
        )
    {
        uint256 length = rewardTokens.length;
        pendingBonusRewards = new uint256[](length);
        for (uint256 i = 0; i < length; i++) {
            pendingBonusRewards[i] = earned(_account, rewardTokens[i]);
        }

        return pendingBonusRewards;
    }

    function getStakingToken() external view returns (address) {
        return stakingToken;
    }

    /* ============ External Functions ============ */

    /// @notice Updates the reward information for one account
    /// @param _account Address account
    function updateFor(address _account) override external {
        _updateFor(_account);
    }

    /// @notice Calculates and sends reward to user. Only callable by masterMagpie
    /// @param _account Address account
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

**File:** rewards/BaseRewardPool.sol (L276-284)
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

**File:** rewards/BaseRewardPool.sol (L286-295)
```text
    /* ============ Internal Functions ============ */

    function _updateFor(address _account) internal {
        uint256 length = rewardTokens.length;
        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            userRewards[rewardToken][_account] = earned(_account, rewardToken);
            userRewardPerTokenPaid[rewardToken][_account] = rewardPerToken(rewardToken);
        }
    }
```

**File:** rewards/BaseRewardPool.sol (L297-320)
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
        if (this.totalStaked() == 0) {
            rewardInfo.queuedRewards += _amountReward;
        } else {
            if (rewardInfo.queuedRewards > 0) {
                _amountReward += rewardInfo.queuedRewards;
                rewardInfo.queuedRewards = 0;
            }
            rewardInfo.rewardPerTokenStored =
                rewardInfo.rewardPerTokenStored +
                (_amountReward * 10**stakingDecimals()) /
                this.totalStaked();
        }
        emit RewardAdded(_amountReward, _rewardToken);
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L291-327)
```text
    /// @notice Sends new rewards to be distributed to the users staking. Only callable by manager
    /// @param _amountReward Amount of reward token to be distributed
    /// @param _rewardToken Address reward token
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
        IERC20Metadata(_rewardToken).safeTransferFrom(
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
                (_amountReward * 10**vlMGPDecimal) / totalStaked();
        }
        emit RewardAdded(_amountReward, _rewardToken);
        return true;
    }
```

**File:** rewards/mWOMSVBaseRewarder.sol (L303-328)
```text
    /* ============ Internal Functions ============ */

    function _provisionReward(uint256 _amountReward, address _rewardToken) internal {
        IERC20Metadata(_rewardToken).safeTransferFrom(
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
                (_amountReward * 10**mWOMSVDecimal) / totalStaked();
        }
        emit RewardAdded(_amountReward, _rewardToken);
    }
```

**File:** rewards/DelegateVoteRewardPool.sol (L181-203)
```text
    ) internal {
        if (!isRewardToken[_rewardToken]) {
            rewardTokens.push(_rewardToken);
            isRewardToken[_rewardToken] = true;
        }
        Reward storage rewardInfo = rewards[_rewardToken];
        rewardInfo.historicalRewards =
            rewardInfo.historicalRewards +
            _amountReward;
        if (totalSupply == 0) {
            rewardInfo.queuedRewards += _amountReward;
        } else {
            if (rewardInfo.queuedRewards > 0) {
                _amountReward += rewardInfo.queuedRewards;
                rewardInfo.queuedRewards = 0;
            }
            rewardInfo.rewardPerTokenStored =
                rewardInfo.rewardPerTokenStored +
                (_amountReward * 10 ** this.stakingDecimals()) /
                totalSupply;
        }
        emit RewardAdded(_amountReward, _rewardToken);
    }
```
