### Title
Direct receipt-token transfer to MasterMagpie permanently dilutes reward-per-token accounting - ([File: rewards/BaseRewardPoolV2.sol])

### Summary
`BaseRewardPoolV2.totalStaked()` (and the equivalent in `BaseRewardPool.sol`) computes the pool's total stake by reading the raw ERC20 balance of the `stakingToken` (the WombatStaking receipt token) held by `operator` (`MasterMagpie`), instead of relying on `MasterMagpie`'s internal accounting of user deposits. [1](#0-0)  Any wallet holding the receipt token can transfer it directly to `MasterMagpie` without going through `WombatPoolHelper.deposit`/`depositFor`, inflating `totalStaked()` while `MasterMagpie`'s per-user `userInfo[stakingToken][account].amount` mapping — which is what `balanceOf()` in the rewarder actually reads for reward attribution — remains unchanged. [2](#0-1) [3](#0-2) 

### Finding Description
The receipt token is a plain `MintableERC20`; only `mint`/`burn` are `onlyOwner`-gated, while `transfer`/`transferFrom` are fully open to any holder. [4](#0-3)  This token is minted to users' pool helper on deposit and staked into `MasterMagpie` via `depositFor`, which increments `user.amount`/`user.available` in an internal mapping rather than relying on the contract's raw token balance. [5](#0-4) [6](#0-5) 

`BaseRewardPoolV2._provisionReward` (invoked by `queueNewRewards`/`donateRewards`) computes the new `rewardPerTokenStored` by dividing the incoming reward amount by `totalStaked()`: [7](#0-6) 

Because `totalStaked()` reads `IERC20(stakingToken).balanceOf(operator)` directly rather than the sum of `userInfo` amounts tracked by `MasterMagpie`, any ordinary wallet holding receipt tokens can call `stakingToken.transfer(masterMagpie, amount)` directly (bypassing `WombatPoolHelper`/`WombatStaking`), instantly inflating the denominator used in every subsequent reward distribution for that pool. This artificially and permanently lowers `rewardPerTokenStored` growth for every future `queueNewRewards`/`donateRewards` call, since the inflated balance is never attributed to any user's `balanceOf()` (which is sourced from `IMasterMagpie.stakingInfo`, i.e., the `userInfo` mapping) and cannot be reversed — there is no mechanism to detect or remove "phantom" balance from `totalStaked()`. [2](#0-1) [8](#0-7) 

The same pattern exists in the legacy `BaseRewardPool.sol`. [9](#0-8) [10](#0-9) 

This mirrors the "incorrect dividends" bug class in the referenced report: a share/dividend calculation is driven by a manipulable raw balance rather than the protocol's authoritative internal accounting of legitimate stakers, letting anyone permanently distort the per-share payout rate.

### Impact Explanation
Every subsequent reward top-up (`queueNewRewards`, `donateRewards`, or auto-harvest flows that call `_toMasterWomAndSendReward`) will compute a `rewardPerTokenStored` increment using an inflated `totalStaked()`. This means a fraction of every future reward distribution is permanently and irrecoverably diluted away from legitimate stakers — no user's `balanceOf()` accounts for the donated tokens, so no one is ever credited that portion of yield; it becomes permanently unclaimable/lost. This is a low-cost, repeatable griefing vector against all stakers of the affected pool, constituting a permanent freeze/loss of unclaimed yield for existing and future stakers.

### Likelihood Explanation
Any wallet that holds (or briefly acquires) a receipt token can trigger this by executing a single plain `transfer()` to the `MasterMagpie` address — no privileged role, governance action, or oracle manipulation is required. It is directly reachable from an ordinary user's transaction and costs only the price of acquiring/holding a small amount of receipt token.

### Recommendation
Replace `totalStaked()`'s reliance on the raw ERC20 `balanceOf(operator)` with `MasterMagpie`'s authoritative internal total (e.g., track and expose a `pool.totalStaked` accumulator that is incremented/decremented only inside `_deposit`/`_withdraw`), so it cannot be manipulated by unsolicited token transfers.

### Proof of Concept
1. A user holds (or acquires) `X` amount of a Wombat pool's receipt token (`stakingToken` in `BaseRewardPoolV2`), for example by a normal deposit through `WombatPoolHelper`.
2. Instead of (or in addition to) staking through the helper, the user calls `receiptToken.transfer(masterMagpie, X)` directly.
3. `totalStaked()` for that pool's rewarder now returns the real staked amount plus `X`, while no `userInfo[stakingToken][*].amount` reflects this extra `X`. [1](#0-0) 
4. The next time rewards are queued via `queueNewRewards`/`donateRewards` (e.g., from routine harvesting), `rewardPerTokenStored` is computed as `(_amountReward * 10**decimals) / totalStaked()` — permanently smaller than it should be because of the inflated denominator. [11](#0-10) 
5. Legitimate stakers' `earned()` values, computed from `rewardPerToken()` minus `userRewardPerTokenPaid`, are permanently reduced for every reward cycle going forward, with the diluted portion never recoverable by anyone. [12](#0-11)

### Citations

**File:** rewards/BaseRewardPoolV2.sol (L124-128)
```text
    /// @notice Returns current amount of staked tokens
    /// @return Returns current amount of staked tokens
    function totalStaked() public override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L130-136)
```text
    /// @notice Returns amount of staked tokens in master magpie by account
    /// @param _account Address account
    /// @return Returns amount of staked tokens by account
    function balanceOf(address _account) public override virtual view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(operator).stakingInfo(stakingToken, _account);
        return staked;
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L145-151)
```text
    function rewardPerToken(address _rewardToken)
        public
        override
        view
        returns (uint256)
    {
        return rewards[_rewardToken].rewardPerTokenStored;
```

**File:** rewards/BaseRewardPoolV2.sol (L301-313)
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

**File:** rewards/MasterMagpie.sol (L481-505)
```text
    /// @notice internal function to deal with deposit staking token
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
```

**File:** libraries/MintableERC20.sol (L8-21)
```text
contract MintableERC20 is ERC20, Ownable {
    /*
    The ERC20 deployed will be owned by the others contracts of the protocol, specifically by
    MasterMagpie and WombatStaking, forbidding the misuse of these functions for nefarious purposes
    */
    constructor(string memory name_, string memory symbol_) ERC20(name_, symbol_) {} 

    function mint(address account, uint256 amount) external virtual onlyOwner {
        _mint(account, amount);
    }

    function burn(address account, uint256 amount) external virtual onlyOwner {
        _burn(account, amount);
    }
```

**File:** wombat/WombatStaking.sol (L242-270)
```text
    function deposit(
        address _lpAddress,
        uint256 _amount,
        uint256 _minimumLiquidity,
        address _for,
        address _from
    ) nonReentrant whenNotPaused _onlyActivePoolHelper(_lpAddress) external {
        // Get information of the Pool of the token
        Pool storage poolInfo = pools[_lpAddress];
        address depositToken = poolInfo.depositToken;
        IERC20(depositToken).safeTransferFrom(_from, address(this), _amount);

        IERC20(depositToken).safeApprove(poolInfo.depositTarget, _amount);
        uint256 beforeBalance = IERC20(poolInfo.lpAddress).balanceOf(address(this));
        IWombatPool(poolInfo.depositTarget).deposit(
            depositToken,
            _amount,
            _minimumLiquidity,
            address(this),
            block.timestamp,
            false
        );

        uint256 lpReceived = IERC20(poolInfo.lpAddress).balanceOf(address(this)) - beforeBalance;
        _toMasterWomAndSendReward(_lpAddress, lpReceived, true); // triggers harvest from wombat exchange
        // update variables
        IMintableERC20(poolInfo.receiptToken).mint(msg.sender, lpReceived);
        emit NewDeposit(_for, depositToken, _amount, poolInfo.receiptToken, lpReceived);
    }
```

**File:** rewards/BaseRewardPool.sol (L124-136)
```text
    /// @notice Returns current amount of staked tokens
    /// @return Returns current amount of staked tokens
    function totalStaked() external override virtual view returns (uint256) {
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

**File:** rewards/BaseRewardPool.sol (L297-318)
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
```
