### Title
Time-lock on batch-deposited AnkrBNB stake can be bypassed by calling `MasterMagpie.withdraw()` directly, defeating `AnkrBNBPoolHelper`'s vesting/lock enforcement - (File: wombat/AnkrBNBPoolHelper.sol, rewards/MasterMagpie.sol)

### Summary
`AnkrBNBPoolHelper` enforces a lock (`unlockTime` / `lockedAmount`) on staking-token balances that were credited to a user via `batchDepositLPFor`, but this check only exists inside `AnkrBNBPoolHelper.withdraw()`. The underlying `MasterMagpie.withdraw()` function — a fully permissionless entry point any wallet can call directly for any staking token it holds "available" balance in — has no knowledge of, and does not enforce, that lock. This mirrors the reported bug class: a balance/invariant check ("lock must be respected") is implemented in one code path but omitted from a functionally equivalent sibling path that reaches the same underlying state change.

### Finding Description
`AnkrBNBPoolHelper.batchDepositLPFor` (privileged, `ankrOperator`-only) mints and stakes receipt tokens for beneficiaries and separately tracks a per-user lock: [1](#0-0) 

Only `AnkrBNBPoolHelper.withdraw()` checks this lock, and only *after* already calling `_unstake` (i.e., `MasterMagpie.withdrawFor`): [2](#0-1) 

However, `MasterMagpie.withdraw(_stakingToken, _amount)` is a separate, fully public, unprivileged function that any wallet can call directly for the same `stakingToken` (the WombatStaking receipt token used by `AnkrBNBPoolHelper`), since batch deposits credit `user.available` exactly like a normal deposit: [3](#0-2) [4](#0-3) 

The internal `_harvestAndUnstake` used by this path validates only `user.available < _amount`; it has no concept of `AnkrBNBPoolHelper.lockedAmount`/`unlockTime`, because that state lives entirely inside `AnkrBNBPoolHelper`, not `MasterMagpie`: [5](#0-4) 

Because `withdraw()` (unlike `withdrawFor`) carries no `_onlyPoolHelper` restriction, an ordinary wallet that received a locked batch deposit can simply skip `AnkrBNBPoolHelper.withdraw()` entirely and call `MasterMagpie.withdraw(stakingToken, amount)` directly, receiving the raw receipt token into its own wallet without the lock check ever executing.

### Impact Explanation
The lock/vesting guarantee that `AnkrBNBPoolHelper` is designed to enforce for batch-distributed (airdrop/vesting-style) stakes is completely bypassable by the recipient wallet itself, using a standard, unprivileged MasterMagpie function. This defeats the intended time-based release schedule for locked funds, letting a beneficiary obtain and dispose of (or hold outside the locking contract) receipt tokens representing locked value before `unlockTime`, which is exactly the vesting/lock-slot bypass class explicitly listed as in-scope.

### Likelihood Explanation
High — the path requires no special privileges. Any address that has ever received a batch deposit via `batchDepositLPFor` (or any address whose `user.available` balance for that staking token is nonzero) can trivially call the standard, documented `MasterMagpie.withdraw()` function instead of going through the pool helper. No governance or admin cooperation is needed to trigger the bypass.

### Recommendation
Move lock enforcement into `MasterMagpie` itself (e.g., a lock-aware mapping consulted inside `_harvestAndUnstake`), or restrict the generic `withdraw()`/`_withdraw` path for staking tokens that have associated pool-helper-level lock semantics so that it cannot be invoked without routing through the pool helper's lock check.

### Proof of Concept
1. `ankrOperator` calls `AnkrBNBPoolHelper.batchDepositLPFor(...)`, crediting `lockedAmount[user] = X` and calling `MasterMagpie.depositFor(stakingToken, X, user)`, which sets `user.available[stakingToken][user] += X` [6](#0-5) .
2. Before `unlockTime`, the user calls `MasterMagpie.withdraw(stakingToken, X)` directly (no pool-helper interaction) [7](#0-6) .
3. `_harvestAndUnstake` only checks `user.available < _amount`, which passes, and the raw receipt token is transferred to the user, entirely skipping the `unlockTime`/`lockedAmount` check that exists solely in `AnkrBNBPoolHelper.withdraw()` [8](#0-7) .

### Citations

**File:** wombat/AnkrBNBPoolHelper.sol (L113-134)
```text
    function batchDepositLPFor(uint256 _lpAmount, address[] calldata _for, uint256[] calldata _ratios) external {
        if (msg.sender != ankrOperator) revert NotAllowed();
        if (_for.length != _ratios.length) revert LengthMisMatch();
        
        uint256 totalRatio=0;
        for(uint256 i=0; i<_ratios.length; ++i){
            totalRatio+=_ratios[i];
        }
        if(totalRatio != DENOMINATOR) revert NotAllowed();

        uint256 beforeDeposit = IERC20(stakingToken).balanceOf(address(this));
        IWombatStaking(wombatStaking).depositLP(lpToken, _lpAmount, msg.sender);
        uint256 lpAmount = IERC20(stakingToken).balanceOf(address(this)) - beforeDeposit;

        IERC20(stakingToken).safeApprove(masterMagpie, lpAmount);

        for (uint256 i = 0; i < _for.length; i++) {
            uint256 amount = lpAmount * _ratios[i] / DENOMINATOR;
            lockedAmount[_for[i]] += amount;
            IMasterMagpie(masterMagpie).depositFor(stakingToken, amount, _for[i]);
            emit NewBatchDeposit(_for[i], amount);
        }
```

**File:** wombat/AnkrBNBPoolHelper.sol (L160-177)
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
        uint256 rest = this.balance(msg.sender);
        if (unlockTime > block.timestamp && lockedAmount[msg.sender] > rest) revert NotAllowed();
        //  last burn the staking token withdrawn from Master Magpie
        IWombatStaking(wombatStaking).burnReceiptToken(lpToken, _liquidity);


        emit NewWithdraw(msg.sender, _liquidity);
    }
```

**File:** rewards/MasterMagpie.sol (L341-346)
```text
    /// @notice Withdraw staking tokens from Master Mgapie.
    /// @param _stakingToken Staking token of the pool
    /// @param _amount amount to withdraw
    function withdraw(address _stakingToken, uint256 _amount) external whenNotPaused nonReentrant {
        _withdraw(_stakingToken, msg.sender, _amount, false);
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

**File:** rewards/MasterMagpie.sol (L507-534)
```text
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
