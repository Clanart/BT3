### Title
Just-in-time deposit before `donateRewards`/`queueNewRewards` lets an attacker skim reward-pool yield meant for long-term stakers - (File: rewards/BaseRewardPool.sol)

### Summary
`BaseRewardPool` (and its variants `BaseRewardPoolV2`, `mWOMSVBaseRewarder`) update `rewardPerTokenStored` instantaneously and proportionally to whatever `totalStaked()` happens to be at the moment new rewards are provisioned. Because deposits/withdrawals through `MasterMagpie.deposit`/`withdraw` have no cooldown, vesting, or minimum-stake-duration requirement, and because `donateRewards()` is a completely permissionless, unauthenticated function, any wallet can front-run (or simply time) an upcoming reward provisioning event: deposit a large amount of the staking token right before rewards are credited, and withdraw immediately after collecting a share of the reward. This mirrors the analog report's "frontrun a state change that revalues an asset" bug class (`updateCollectionPrice`), applied here to `rewardPerTokenStored` instead of NFT price.

### Finding Description
`_provisionReward` in `rewards/BaseRewardPool.sol` credits new rewards to `rewardPerTokenStored` using the pool's current `totalStaked()` at the time of the call, with no time-weighting or streaming of the reward over time: [1](#0-0) 

Both the manager-only `queueNewRewards` and the fully public `donateRewards` funnel into this same instant-credit logic: [2](#0-1) 

Because `earned()`/`rewardPerToken()` snapshot the account's `balanceOf` at read time and `userRewardPerTokenPaid`, a user's share of the reward chunk is fixed entirely by how much they are staked at the moment the reward is provisioned, not by how long they have been staked: [3](#0-2) 

`MasterMagpie.deposit`/`withdraw` are unprivileged, no-cooldown entry points, so a wallet can deposit immediately before a reward provisioning transaction and withdraw immediately after, capturing a share of the reward proportional to its (temporarily inflated) stake: [4](#0-3) 

This is the same root cause pattern as `updateCollectionPrice()` in the external report: a state variable that materially changes user economics (there: NFT price; here: `rewardPerTokenStored`) is updated atomically in one transaction with no protection against adjacent transactions from unrelated unprivileged wallets, so an attacker can transact immediately before/after to extract value that should have accrued to the pre-existing, long-term participants.

### Impact Explanation
Reward tokens queued via `queueNewRewards`/`donateRewards` are meant to accrue to stakers in proportion to their staked duration/exposure. A just-in-time depositor with no genuine economic exposure to the pool can capture a disproportionate share of freshly queued rewards and immediately exit, diluting and effectively stealing unclaimed yield from the genuine long-term stakers who were staked when the reward-generating activity (e.g., harvested protocol fees, bribes) actually occurred. This satisfies the "theft of unclaimed yield" impact category, since honest stakers who were present the whole time receive a diminished share of the reward pool relative to what they should have accrued.

### Likelihood Explanation
`donateRewards` requires no privileged role at all - any address can call it, and any address can freely `deposit`/`withdraw` in the same or adjacent blocks with no lock, cooldown, or streaming/vesting of rewards. An attacker only needs to watch the mempool for large `queueNewRewards`/`donateRewards` calls (these are common, periodic operations for a live protocol distributing harvested fees) or can even coordinate with their own `donateRewards` call to arbitrage timing across multiple reward pools. This requires no governance or admin cooperation, making it directly reachable from an ordinary wallet.

### Recommendation
Stream newly provisioned rewards over a fixed duration (similar to Synthetix-style `rewardRate`/`periodFinish` mechanics) instead of crediting `rewardPerTokenStored` instantly and in full. Alternatively/additionally, introduce a minimum staking duration or withdrawal cooldown in `MasterMagpie` so that deposits cannot be immediately reversed after a reward distribution, removing the incentive for just-in-time staking.

### Proof of Concept
1. Attacker monitors the mempool and observes a pending `queueNewRewards(amountReward, rewardToken)` (or `donateRewards`) transaction targeting a `BaseRewardPool`.
2. Attacker submits `MasterMagpie.deposit(stakingToken, largeAmount)` with higher gas to land immediately before the reward transaction.
3. The reward transaction executes `_provisionReward`, computing `rewardInfo.rewardPerTokenStored += (_amountReward * 10**decimals) / totalStaked()`, where `totalStaked()` now includes the attacker's just-added `largeAmount` [5](#0-4) .
4. Attacker immediately calls `MasterMagpie.withdraw(stakingToken, largeAmount)` and claims rewards via `getReward`, receiving a share of the just-queued reward proportional to `largeAmount / totalStaked()` despite having been staked for effectively zero time [6](#0-5) .
5. Honest long-term stakers, who were diluted by the attacker's temporary inflation of `totalStaked()` at the moment of credit, receive a correspondingly smaller share of the reward than they would have without the attacker's presence.

### Citations

**File:** rewards/BaseRewardPool.sol (L173-185)
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
```

**File:** rewards/BaseRewardPool.sol (L219-240)
```text
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

**File:** rewards/BaseRewardPool.sol (L258-284)
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

        _provisionReward(_amountReward, _rewardToken);
        return true;
    }

    /// @notice Sends new rewards to be distributed to the users staking. Only possible to donate already registered token
    /// @param _amountReward Amount of reward token to be distributed
    /// @param _rewardToken Address reward token
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
    }
```

**File:** rewards/BaseRewardPool.sol (L297-319)
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
```

**File:** rewards/MasterMagpie.sol (L334-346)
```text
    /// @notice Deposits staking token to the pool, updates pool and distributes rewards
    /// @param _stakingToken Staking token of the pool
    /// @param _amount Amount to deposit to the pool
    function deposit(address _stakingToken, uint256 _amount) external whenNotPaused nonReentrant {
        _deposit(_stakingToken, msg.sender, _amount, false);
    }

    /// @notice Withdraw staking tokens from Master Mgapie.
    /// @param _stakingToken Staking token of the pool
    /// @param _amount amount to withdraw
    function withdraw(address _stakingToken, uint256 _amount) external whenNotPaused nonReentrant {
        _withdraw(_stakingToken, msg.sender, _amount, false);
    }
```
