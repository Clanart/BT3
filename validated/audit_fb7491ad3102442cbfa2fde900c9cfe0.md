This is a valid finding: `BaseRewardPoolV2`/`BaseRewardPool` distribute rewards instantly via `rewardPerTokenStored`, computed from `totalStaked()` snapshotted at the moment `queueNewRewards` executes, with no time-weighting or lockup — so pre-vote deposits and post-vote withdrawals let an attacker capture bribe rewards without contributing to the voting-period stake.

### Title
Front-runnable instant reward-per-token distribution allows theft of bribe yield via deposit-before-vote/withdraw-after-vote (WombatStaking.vote -> BaseRewardPoolV2.queueNewRewards) - (File: wombat/WombatStaking.sol, rewards/BaseRewardPoolV2.sol)

### Summary
`WombatStaking.vote` forwards harvested bribe rewards to `IBaseRewardPool(_rewarders[i]).queueNewRewards(rewardAmount, token)` [1](#0-0) , which immediately bumps `rewardPerTokenStored` using the pool's `totalStaked()` at that exact block [2](#0-1) . Because reward accrual is a one-shot balance-weighted snapshot rather than a time-streamed (e.g. Synthetix-style duration) distribution, any staked balance present at the instant of `queueNewRewards` receives a proportional share of the entire bribe batch, regardless of how long that balance was actually staked.

### Finding Description
`WombatPoolHelper(V2).deposit()` mints receipt tokens and stakes them into `MasterMagpie` via `depositFor` [3](#0-2) , and `withdraw()` symmetrically unstakes and burns [4](#0-3) . `BaseRewardPoolV2.balanceOf` and `totalStaked` read live `MasterMagpie` staking balances [5](#0-4) .

When `WombatBribeManager.castVotes` calls `wombatStaking.vote(...)`, harvested bribe rewards are pushed into the rewarder via `queueNewRewards` [6](#0-5) . Inside `_provisionReward`, if `totalStaked() != 0`, the entire new reward amount is divided by the current `totalStaked()` and added to `rewardPerTokenStored` in a single step [7](#0-6) . There is no reward-duration/streaming mechanism and no minimum staking-period requirement — `earned()` is computed purely from the user's current balance times the delta in `rewardPerTokenStored` since their last checkpoint [8](#0-7) .

Exploit flow:
1. Attacker observes a pending `castVotes()`/`vote()` transaction in the mempool.
2. Attacker front-runs it with `WombatPoolHelper.deposit()` (or `depositFor`) for the target pool's receipt token, increasing `totalStaked()` right before the reward injection.
3. `vote()` executes, calling `queueNewRewards`, which computes `rewardPerTokenStored` using the now-inflated `totalStaked()` — the attacker's fresh deposit dilutes other stakers' share and simultaneously entitles the attacker to a proportional cut of the whole `rewardAmount`.
4. Attacker calls `withdraw(..., claim=true)` (or `getReward`) immediately after, capturing their earned share and exiting.

No modifier, `nonReentrant` guard, receipt-token vesting, or time-weighted accounting in `BaseRewardPoolV2`/`BaseRewardPool` prevents this, since reward accrual is a single atomic snapshot rather than a stream — the same class of issue as classic "flash deposit to steal instant reward drop" bugs in Synthetix-style reward pools that lack `rewardsDuration`/`lastUpdateTime` streaming logic.

### Impact Explanation
This is direct theft of unclaimed yield: an attacker with zero voting-period exposure to the pool can capture a share of the bribe reward proportional to `theirDeposit / totalStaked` at the exact block of `queueNewRewards`, diluting genuine long-term stakers' entitlement. This matches the "theft of unclaimed yield" impact class stated in the prompt's scoped impact.

### Likelihood Explanation
Preconditions are fully within an unprivileged attacker's reach: `WombatBribeManager.castVotes`/`harvestSinglePool` and `WombatStaking.vote` are triggerable by any caller (`castVotes` is public, `msg.sender` becomes `caller` for fee purposes) [9](#0-8) , and the transaction is visible in the public mempool prior to inclusion. The attacker needs only enough capital to deposit into the receipt token pool and pay gas for a same-block or adjacent-block front-run/back-run pair, and the attack is fully repeatable every time bribes are cast.

### Recommendation
Convert `BaseRewardPool`/`BaseRewardPoolV2` reward distribution to a time-weighted streaming model (e.g., Synthetix `StakingRewards` style with `rewardsDuration`, `lastUpdateTime`, `rewardRate` accrued per second) instead of an instantaneous `rewardPerTokenStored` bump based on the current `totalStaked()` snapshot. Alternatively, enforce a minimum staking lock/cooldown before a deposit becomes reward-eligible for freshly queued rewards, or snapshot `totalStaked()`/eligible balances prior to the block in which `queueNewRewards` is called.

### Proof of Concept
Foundry fork test plan:
1. Deploy/fork `WombatStaking`, `BaseRewardPoolV2` for a target lp pool, `WombatBribeManager`, and `MasterMagpie` with an existing long-term staker (Alice) holding receipt tokens staked for many blocks.
2. Mempool simulation: construct the pending `castVotes()`/`wombatStaking.vote()` call that will push a known `rewardAmount` of `token` into the pool's rewarder.
3. Attacker (Bob), in the block immediately preceding the vote tx, calls `WombatPoolHelper.deposit(amount, minLiquidity)` to stake receipt tokens equal to e.g. Alice's full stake.
4. Execute the `vote()`/`castVotes()` tx, triggering `queueNewRewards(rewardAmount, token)`.
5. Immediately after, Bob calls `WombatPoolHelper.withdraw(amount, minAmount)` (or `MasterMagpie.withdrawFor`+`getReward`) to claim rewards and exit.
6. Assert: `bribeRewarder.earned(bob, token) > 0` and approximately `rewardAmount * bobStake / (aliceStake + bobStake)`, despite Bob having zero staking-period duration overlapping the actual voting epoch — proving disproportionate capture relative to actual stake-time versus Alice, who held the position the entire epoch.
7. Assert Alice's `earned()` is correspondingly diluted below what she would have received absent Bob's front-run deposit, demonstrating conservation-invariant violation (yield redirected away from genuine stakers to a flash staker).

### Citations

**File:** wombat/WombatStaking.sol (L363-411)
```text
    function vote(
        address[] calldata _lpVote,
        int256[] calldata _deltas,
        address[] calldata _rewarders,
        address caller
    ) external returns (IERC20[][] memory rewardTokens, uint256[][] memory callerFeeAmounts) {
        if(msg.sender != bribeManager)
            revert OnlyBribeMamager();
            
        if (_lpVote.length != _rewarders.length || _lpVote.length != _deltas.length)
            revert LengthMismatch();
        uint256[][] memory rewardAmounts = voter.vote(_lpVote, _deltas);
        rewardTokens = new IERC20[][](rewardAmounts.length);
        callerFeeAmounts = new uint256[][](rewardAmounts.length);

        for (uint256 i; i < rewardAmounts.length; i++) {

            address bribesContract = address(voter.infos(_lpVote[i]).bribe);

            if (bribesContract != address(0)) {
                rewardTokens[i] = IWombatBribe(bribesContract).rewardTokens();
                callerFeeAmounts[i] = new uint256[](rewardAmounts[i].length);

                for (uint256 j; j < rewardAmounts[i].length; j++) {
                    uint256 rewardAmount = rewardAmounts[i][j];
                    uint256 callerFeeAmount = 0;

                    if (rewardAmount > 0) {
                        // if reward token is bnb, wrap it first
                        if (address(rewardTokens[i][j]) == address(0)) {
                            Address.sendValue(payable(wbnb), rewardAmount);
                            rewardTokens[i][j] = IERC20(wbnb);
                        }

                        uint256 protocolFee = (rewardAmount * bribeProtocolFee) / DENOMINATOR;

                        if (protocolFee > 0) {
                            IERC20(rewardTokens[i][j]).safeTransfer(bribeFeeCollector, protocolFee);
                        }

                        if (caller != address(0) && bribeCallerFee != 0) {
                            callerFeeAmount = (rewardAmount * bribeCallerFee) / DENOMINATOR;
                            IERC20(rewardTokens[i][j]).safeTransfer(bribeManager, callerFeeAmount);
                        }

                        rewardAmount -= protocolFee;
                        rewardAmount -= callerFeeAmount;
                        IERC20(rewardTokens[i][j]).safeApprove(_rewarders[i], rewardAmount);
                        IBaseRewardPool(_rewarders[i]).queueNewRewards(rewardAmount, address(rewardTokens[i][j]));
```

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

**File:** rewards/BaseRewardPoolV2.sol (L316-321)
```text
    function _earned(address _account, address _rewardToken, uint256 _userShare) internal view returns (uint256) {
        return ((_userShare *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                10**stakingTokenDecimals) + userRewards[_rewardToken][_account];
    }
```

**File:** wombat/WombatPoolHelperV2.sol (L132-147)
```text
    function withdraw(uint256 _liquidity, uint256 _minAmount) external override {
        // we have to withdraw from wombat exchange to harvest reward to base rewarder
        IWombatStaking(wombatStaking).withdraw(
            lpToken,
            _liquidity,
            _minAmount,
            msg.sender
        );
        // then we unstake from master wombat to trigger reward distribution from basereward
        _unstake(_liquidity, msg.sender);
        //  last burn the staking token withdrawn from Master Magpie
        IWombatStaking(wombatStaking).burnReceiptToken(lpToken, _liquidity);


        emit NewWithdraw(msg.sender, _liquidity);
    }
```

**File:** wombat/WombatPoolHelperV2.sol (L155-162)
```text
    function _deposit(uint256 _amount, uint256 _minimumLiquidity, address _for, address _from) internal {
        uint256 beforeDeposit = IERC20(stakingToken).balanceOf(address(this));
        IWombatStaking(wombatStaking).deposit(lpToken, _amount, _minimumLiquidity, _for, _from);
        uint256 afterDeposit = IERC20(stakingToken).balanceOf(address(this));
        _stake(afterDeposit - beforeDeposit, _for);
        
        emit NewDeposit(_for, _amount);
    }
```

**File:** wombat/WombatBribeManager.sol (L241-276)
```text
    function castVotes(bool swapForBnb)
        override public
        returns (address[][] memory finalRewardTokens, uint256[][] memory finalFeeAmounts)
    {
        lastCastTime = block.timestamp;
        uint256 length = pools.length;
        address[] memory _pools = new address[](length);
        int256[] memory votes = new int256[](length);
        address[] memory rewarders = new address[](length);

        for (uint256 i; i < length; i++) {
            Pool storage pool = poolInfos[pools[i]];
            _pools[i] = pool.poolAddress;
            rewarders[i] = pool.rewarder;

            uint256 currentVote = getVoteForLp(pool.poolAddress);
            uint256 targetVoteInLMGP = pool.totalVoteInVlmgp;
            uint256 targetVote = 0;

            if (totalVlMgpInVote != 0) {
                targetVote = targetVoteInLMGP * totalVotes() / totalVlMgpInVote;
            }

            if (targetVote >= currentVote) {
                votes[i] = int256(targetVote - currentVote);
            } else {
                votes[i] = int256(targetVote) - int256(currentVote);
            }
        }

        (address[][] memory rewardTokens, uint256[][] memory feeAmounts) = wombatStaking.vote(
            _pools,
            votes,
            rewarders,
            msg.sender
        );
```
