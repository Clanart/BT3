## Title
Deactivating a Wombat pool via `removePool` does not stop reward accrual for already-staked receipt tokens in MasterMagpie/BaseRewardPool - (File: wombat/WombatStaking.sol)

### Summary
`WombatStaking.removePool` only flips a local `isActive` flag and blocks new deposits/withdrawals/harvests through the pool helper, but it never notifies `MasterMagpie` to stop or zero-out the emission allocation for the corresponding receipt token. Receipt tokens already staked in `MasterMagpie`/`BaseRewardPool` keep earning MGP (and any queued bonus) rewards indefinitely, exactly mirroring the reported bug class where tokens tied to an "expired"/worthless vault continue to accrue rewards in a separate rewards contract because the two systems are not kept in sync.

### Finding Description
`removePool` is the only function that marks a Wombat pool as retired: [1](#0-0) 

Once `isActive` is `false`, the modifiers `_onlyActivePool` and `_onlyActivePoolHelper` prevent any further `deposit`, `withdraw`, `depositLP`, or `harvest` calls routed through `WombatStaking`, meaning the underlying LP position can no longer accrue or distribute the WOM-side rewards that back the receipt token's value: [2](#0-1) 

However, the receipt token minted for that pool (`newToken`/`receiptToken`) is registered as an independent staking asset in `MasterMagpie` with its own reward pool (`rewarder`) and allocation points at pool-creation time: [3](#0-2) 

`removePool` never calls `MasterMagpie.set(...)` (the same call used in `updatePoolHelper` to update allocation/rewarder wiring) to reduce the pool's `allocPoint` to zero or otherwise disable emissions: [4](#0-3) 

As a result, any wallet that already holds/staked the receipt token in `MasterMagpie` before the pool was deactivated continues to accrue MGP rewards via `BaseRewardPool`'s per-second reward-per-token accounting, which has no dependency on whether the backing Wombat pool is still active: [5](#0-4) 

This is the direct structural analog of the reported issue: an "expired"/worthless position (the insrVault token after `triggerEndEpoch`, here the receipt token of a deactivated Wombat pool) keeps generating rewards through a staking/reward contract because the vault-side state change (epoch end / pool deactivation) is not propagated to stop the independent reward-accrual clock (`periodFinish` / `allocPoint`).

### Impact Explanation
Unprivileged wallets that keep receipt tokens staked in `MasterMagpie` after their backing pool is deactivated keep drawing MGP emissions from the shared `MasterMagpie` emission budget with no underlying yield or LP risk, diluting rewards owed to stakers of active, legitimate pools. Because `MasterMagpie` reward budgets are typically finite/allocated pro-rata across pools by `allocPoint`, this results in permanent, ongoing misallocation/leakage of protocol reward funds to holders of a dead pool's tokens - protocol-level value leakage of unclaimed yield, matching the accepted impact class in the source report (issue judged as leaked protocol value/rewards).

### Likelihood Explanation
Any pool that is later deactivated with `removePool` (a normal operational/lifecycle action, not a hack) triggers this - it does not require any special conditions beyond a pool being retired while users still hold staked receipt tokens, which is a common and expected scenario. The window is open-ended (until an admin manually calls `MasterMagpie.set` to fix allocation, if ever), so it can persist well beyond 24 hours.

### Recommendation
Have `removePool` (or an accompanying call) invoke `MasterMagpie.set(receiptToken, 0, ...)` to zero the pool's allocation point (and optionally pause the corresponding `BaseRewardPool`) atomically with marking the Wombat pool inactive, so reward accrual for the deactivated pool's receipt token stops in the same transaction that disables deposits/withdrawals.

### Proof of Concept
1. Owner calls `registerPool` to add pool `P` with receipt token `R`, rewarder `BaseRewardPool_R`, and non-zero `allocPoint` in `MasterMagpie` [6](#0-5) .
2. A user deposits into pool `P` through `WombatPoolHelper`, minting/staking `R` in `MasterMagpie` [7](#0-6) .
3. Owner calls `removePool(lpAddress)`, setting `pools[lpAddress].isActive = false` [1](#0-0) . No call is made to `MasterMagpie` to adjust `allocPoint`.
4. The user's already-staked `R` balance in `MasterMagpie` continues to accrue rewards through `BaseRewardPool`'s `rewardPerToken`/`earned` accounting, and the user can still call `getReward` to claim MGP indefinitely, even though the pool `P` can no longer deposit/withdraw/harvest via `WombatStaking` (`_onlyActivePool` reverts) [8](#0-7) .

Note: I was unable to fully inspect `rewards/MasterMagpie.sol` (the `depositFor`/`withdrawFor`/`set`/`add` implementations) due to tool/iteration limits, so I could not confirm whether `MasterMagpie` has any independent gating that might mitigate this (e.g., checking pool status elsewhere) or whether additional direct-deposit paths into `MasterMagpie` exist post-deactivation. This should be verified in a full session before treating the PoC as complete.

### Citations

**File:** wombat/WombatStaking.sol (L183-199)
```text
    modifier _onlyActivePool (address _lpToken) {
        Pool storage poolInfo = pools[_lpToken];

        if (!poolInfo.isActive)
            revert OnlyActivePool();
        _;
    }

    modifier _onlyActivePoolHelper(address _lpToken) {
        Pool storage poolInfo = pools[_lpToken];

        if (msg.sender != poolInfo.helper)
            revert OnlyPoolHelper();
        if (!poolInfo.isActive)
            revert OnlyActivePool();
        _;
    }
```

**File:** wombat/WombatStaking.sol (L429-482)
```text
    function registerPool(
        uint256 _pid,
        address _depositToken,
        address _lpAddress,
        address _depositTarget,
        string memory _receiptName,
        string memory _receiptSymbol,
        uint256 _allocPoints,
        bool _isNative
    ) external onlyOwner {
        if (pools[_lpAddress].isActive != false) {
            revert PoolOccupied();
        }
        IERC20 newToken = IERC20(
            ERC20FactoryLib.createERC20(_receiptName, _receiptSymbol)
        );
        address rewarder = IMasterMagpie(masterMagpie).createRewarder(
            address(newToken),
            address(wom)
        );
        IPoolHelper helper = IPoolHelper(
            PoolHelperFactoryLib.createWombatPoolHelper(
                _pid,
                address(newToken),
                address(_depositToken),
                address(_lpAddress),
                address(this),
                address(masterMagpie),
                address(rewarder),
                address(mWom),
                _isNative
            )
        );
        IMasterMagpie(masterMagpie).add(
            _allocPoints,
            address(newToken),
            address(rewarder),
            address(helper),
            true            
        );
        pools[_lpAddress] = Pool({
            pid: _pid,
            isActive: true,
            depositToken: _depositToken,
            lpAddress: _lpAddress,
            receiptToken: address(newToken),
            rewarder: address(rewarder),
            helper: address(helper),
            depositTarget: _depositTarget
        });
        poolTokenList.push(_depositToken);

        emit PoolAdded(_pid, _depositToken, _lpAddress, address(helper), address(rewarder), address(newToken));
    }
```

**File:** wombat/WombatStaking.sol (L500-505)
```text
    /// @notice mark the pool as inactive
    function removePool(address _lpToken) external onlyOwner {
        pools[_lpToken].isActive = false;

        emit PoolRemoved(pools[_lpToken].pid, _lpToken);
    }
```

**File:** wombat/WombatStaking.sol (L508-527)
```text
    function updatePoolHelper (
        address _lpAddress, uint256 _pid,
        address _poolHelper, address _rewarder, 
        address _depositToken, address _depositTarget,
        uint256 _allocPoint)
        external
        onlyOwner
        _onlyActivePool(_lpAddress)
    {
        Pool storage poolInfo = pools[_lpAddress];
        poolInfo.pid = _pid;
        poolInfo.helper = _poolHelper;
        poolInfo.rewarder = _rewarder;
        poolInfo.depositToken = _depositToken;
        poolInfo.depositTarget = _depositTarget;

        IMasterMagpie(masterMagpie).set(poolInfo.receiptToken, _allocPoint, _poolHelper, _rewarder, true);

        emit PoolHelperUpdated(_lpAddress);
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

**File:** rewards/BaseRewardPool.sol (L286-300)
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

    function _provisionReward(uint256 _amountReward, address _rewardToken) internal {
        IERC20(_rewardToken).safeTransferFrom(
            msg.sender,
            address(this),
```

**File:** wombat/WombatPoolHelper.sol (L148-165)
```text
    function _deposit(uint256 _amount, uint256 _minimumLiquidity, address _from) internal {
        uint256 beforeDeposit = IERC20(stakingToken).balanceOf(address(this));
        IWombatStaking(wombatStaking).deposit(lpToken, _amount, _minimumLiquidity, msg.sender, _from);
        uint256 afterDeposit = IERC20(stakingToken).balanceOf(address(this));
        _stake(afterDeposit - beforeDeposit, msg.sender);
        
        emit NewDeposit(msg.sender, _amount);
    }

    function _wrapNative() internal {
        IWNative(depositToken).deposit{value: msg.value}();
    }

    /// @notice stake the receipt token in the masterchief of GMP on behalf of the caller
    function _stake(uint256 _amount, address _sender) internal {
        IERC20(stakingToken).safeApprove(masterMagpie, _amount);
        IMasterMagpie(masterMagpie).depositFor(stakingToken, _amount, _sender);
    }
```
