### Title
Instantaneous-balance reward accounting lets a same-block flash-depositor capture yield meant for time-weighted stakers - ([File: rewards/BaseRewardPool.sol])

### Summary
`BaseRewardPool` computes rewards purely from a lump-sum `rewardPerTokenStored` snapshot and the caller's *current* `balanceOf()`, with no time-weighting of stake duration. Because `WombatBribeManager.castVotes()` / `harvestSinglePool()` are public and unrestricted, an attacker can deposit into `MasterMagpie` right before triggering `queueNewRewards`, then withdraw and claim, capturing a share of the newly injected reward proportional to their instantaneous balance despite zero time staked, diluting the yield that time-weighted stakers should have received.

### Finding Description
`_provisionReward` (rewards/BaseRewardPool.sol:297-320) updates the pool-wide `rewardPerTokenStored` as a single global lump sum: `rewardPerTokenStored += amountReward * 10**decimals / totalStaked()`, evaluated at `totalStaked()` (current live balance) at the moment `queueNewRewards` executes — not integrated over time held by each staker.

`earned()` (rewards/BaseRewardPool.sol:173-185) is:
```
balanceOf(_account) * (rewardPerToken(token) - userRewardPerTokenPaid[token][_account]) / 10**stakingDecimals() + userRewards[token][_account]
```
`balanceOf()` (line 133-136) reads the account's *current* staked amount from `MasterMagpie.stakingInfo`. `_updateFor` (line 288-295), invoked via the `updateReward` modifier and via `MasterMagpie._harvestBaseRewarder`/`_deposit`/`_withdraw`, snapshots `userRewardPerTokenPaid` to the current `rewardPerToken` value whenever a user's balance changes, but is otherwise timeless — there is no per-second accrual, checkpointing of block timestamps, or vesting delay tying reward eligibility to duration of stake.

Exploit path: [1](#0-0)  `deposit()` → `_deposit()` calls `_harvestBaseRewarder` (snapshotting the attacker's `userRewardPerTokenPaid` at pre-reward value with pre-deposit balance) before updating `user.amount`. `WombatBribeManager.castVotes()` [2](#0-1)  is `public` and callable by any unprivileged address, and internally calls `WombatStaking.vote()` [3](#0-2)  which calls `IBaseRewardPool(rewarder).queueNewRewards(rewardAmount, token)`, bumping `rewardPerTokenStored` for the whole pool based on the current `totalStaked()` (which now includes the attacker's freshly deposited balance). The attacker then calls `MasterMagpie.withdraw()`/`claim` path (`_harvestAndUnstake` → `_harvestBaseRewarder`/`_claimBaseRewarder` → `rewarder.getReward`) [4](#0-3)  which computes `earned()` using the *post-deposit* balance against the *pre-to-post-reward* delta in `rewardPerTokenStored`, granting a proportional share of the just-added reward with zero holding time.

None of `nonReentrant`, `whenNotPaused`, or the `updateReward`/`updateFor` snapshotting logic prevent this — they only guard against re-entrancy and stop trading, not against a legitimate sequential same-block deposit → vote → withdraw. This is not a re-entrancy or access-control bug; it is a structural reward-accrual design gap (StakingRewards/MasterChef-style pools are inherently susceptible to this "flash deposit before reward drop" pattern when there is no minimum stake duration, deposit cool-down, or streaming/vesting of newly queued rewards).

### Impact Explanation
This causes theft of unclaimed yield from long-term stakers: reward tokens funded externally (bribes/WOM emissions injected via `queueNewRewards`) that should accrue proportionally to time-weighted stake instead get partially redirected to an attacker who held tokens for effectively zero duration, diluting the proportional share paid to genuine long-term stakers such as staker A in the PoC. This matches the Immunefi impact class "theft of unclaimed yield" for actively staked/vested assets — no principal is stolen, but ongoing yield allocation is misappropriated at the expense of existing stakers, repeatably, every time `castVotes`/`harvestSinglePool` is invoked.

### Likelihood Explanation
The attack requires only holding/acquiring the relevant staking-derived receipt token (obtainable permissionlessly via `WombatPoolHelper.deposit`/`depositLP`), calling `MasterMagpie.deposit`/`depositFor`, then calling the public, unprivileged `WombatBribeManager.castVotes()` (or waiting for/front-running someone else's call to it — since it is also externally callable by "any caller" as stated in the docstring for `harvestSinglePool`), and then withdrawing/claiming. No special privileges, flash loans, or reentrancy are needed; capital requirement is minimal (attacker's share of reward scales with their fraction of `totalStaked()`, so profit is proportional to deposit size and pool reward size, but the tactic is fully repeatable at low cost every voting/harvest cycle).

### Recommendation
Introduce time-weighting or an anti-flash-deposit safeguard in `BaseRewardPool`/`MasterMagpie`, e.g.:
- Require a minimum holding period before a deposit is eligible to earn from newly queued rewards (checkpoint deposit timestamp and exclude balance from `earned()` accrual until `block.timestamp - depositTime >= MIN_STAKE_DURATION`), or
- Stream/vest newly queued rewards linearly over time (Synthetix-style `rewardRate`/`periodFinish` with continuous `rewardPerToken()` accrual based on elapsed time rather than an instantaneous lump-sum jump), so `rewardPerTokenStored` cannot be captured by a same-block deposit, or
- Snapshot `totalStaked()`/eligible balances prior to the block in which `queueNewRewards` is called (e.g., via a checkpoint one block delayed), preventing same-block deposit-then-claim sequences from front-running reward injections.

### Proof of Concept
Foundry test outline:
1. Deploy `MasterMagpie`, `BaseRewardPool` for staking token `X`, and a mock `queueNewRewards` caller (or use `WombatBribeManager`/`WombatStaking` mocks) with `managers[caller] = true`.
2. Staker A deposits 100 `X` into `MasterMagpie` and holds for a full epoch (advance `block.timestamp`).
3. In a single transaction/block for attacker B:
   a. B deposits 1 `X` via `MasterMagpie.deposit(X, 1)`.
   b. Call `rewarder.queueNewRewards(rewardAmount, rewardToken)` (simulating `WombatStaking.vote`/`castVotes` triggering it) — or actually route through `WombatBribeManager.castVotes()` if fully wired.
   c. B calls `MasterMagpie.withdraw(X, 1)` then triggers `getReward`/`claim` for B.
4. Assert `rewarder.earned(B, rewardToken)` (or transferred amount) equals `1 / 101 * rewardAmount` (non-zero), rather than 0, while A's proportional entitlement (`100/101 * rewardAmount`) is correspondingly reduced from what A would have received had B not participated (`100/100 * rewardAmount`).
5. Confirm B's realized reward-per-second-held vastly exceeds A's, demonstrating disproportionate yield capture with zero time-weighted contribution. [5](#0-4) [6](#0-5) [7](#0-6) [2](#0-1) [3](#0-2)

### Citations

**File:** rewards/MasterMagpie.sol (L482-534)
```text
    function _deposit(address _stakingToken, address _account, uint256 _amount, bool _isVlmgp) internal {
        updatePool(_stakingToken);

        PoolInfo storage pool = tokenToPoolInfo[_stakingToken];
        UserInfo storage user = userInfo[_stakingToken][_account];

        if (user.amount > 0) {
            _harvestMGP(_stakingToken, _account);
        }
        _harvestBaseRewarder(_stakingToken, _account);

        user.amount = user.amount + _amount;
        if (!_isVlmgp) {
            user.available = user.available + _amount;
            IERC20(pool.stakingToken).safeTransferFrom(address(msg.sender), address(this), _amount);
        }
        user.rewardDebt = (user.amount * pool.accMGPPerShare) / 1e12;

        if (_amount > 0)
            if (!_isVlmgp)
                emit Deposit(_account, _stakingToken, _amount);
            else
                emit DepositNotAvailable(_account, _stakingToken, _amount);
    }

    /// @notice internal function to deal with withdraw staking token
    function _withdraw(address _stakingToken, address _account, uint256 _amount, bool _isVlMgp) internal {
        _harvestAndUnstake(_stakingToken, _account, _amount, _isVlMgp);

        if (!_isVlMgp)
            IERC20(tokenToPoolInfo[_stakingToken].stakingToken).safeTransfer(address(msg.sender), _amount);
        emit Withdraw(_account, _stakingToken, _amount);
    }

    function _harvestAndUnstake(address _stakingToken, address _account, uint256 _amount, bool _isVlMgp) internal {
        updatePool(_stakingToken);

        UserInfo storage user = userInfo[_stakingToken][_account];

        if (!_isVlMgp && user.available < _amount)
            revert WithdrawAmountExceedsStaked();
        else if(user.amount < _amount && _isVlMgp)
            revert UnlockAmountExceedsLocked();
        
        _harvestMGP(_stakingToken, _account);
        _harvestBaseRewarder(_stakingToken, _account);

        user.amount = user.amount - _amount;
        
        if(!_isVlMgp)
            user.available = user.available - _amount;
        user.rewardDebt = (user.amount * tokenToPoolInfo[_stakingToken].accMGPPerShare) / 1e12;
    }
```

**File:** rewards/MasterMagpie.sol (L601-629)
```text
    /// @notice Harvest MGP for an account
    /// only update the reward counting but not sending them to user
    function _harvestMGP(address _stakingToken, address _account) internal {
        // Harvest MGP
        uint256 pending = _calNewMGP(_stakingToken, _account);
        unClaimedMgp[_stakingToken][_account] += pending;
    }

    /// @notice calculate MGP reward based on current accMGPPerShare
    function _calNewMGP(address _stakingToken, address _account) view internal returns(uint256) {
        UserInfo storage user = userInfo[_stakingToken][_account];
        uint256 pending = (user.amount * tokenToPoolInfo[_stakingToken].accMGPPerShare) /
            1e12 -
            user.rewardDebt;
        return pending;
    }

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
    }
```

**File:** wombat/WombatBribeManager.sol (L241-296)
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

        // comment outs for now since chainlink fails sometimes
        // if (swapForBnb) {
        //     finalFeeAmounts = new uint256[][](1);
        //     finalFeeAmounts[0] = new uint256[](1);
        //     finalFeeAmounts[0][0] = _swapFeesForBnb(rewardTokens, feeAmounts);
        //     finalRewardTokens = new address[][](1);
        //     finalRewardTokens[0] = new address[](1);
        //     finalRewardTokens[0][0] = address(0);
        // } else {
            _forwardRewards(rewardTokens, feeAmounts);
            finalRewardTokens = rewardTokens;
            finalFeeAmounts = feeAmounts;
        // }

        // send rewards to the delegate pool
        if (delegatedPool != address(0)) IDelegateVoteRewardPool(delegatedPool).harvestAll();

        emit VoteCasted(msg.sender, lastCastTime);
    }
```

**File:** wombat/WombatStaking.sol (L363-418)
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
                    }

                    callerFeeAmounts[i][j] = callerFeeAmount;
                }
            }
        }
    }
```

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

**File:** rewards/BaseRewardPool.sol (L288-320)
```text
    function _updateFor(address _account) internal {
        uint256 length = rewardTokens.length;
        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            userRewards[rewardToken][_account] = earned(_account, rewardToken);
            userRewardPerTokenPaid[rewardToken][_account] = rewardPerToken(rewardToken);
        }
    }

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
