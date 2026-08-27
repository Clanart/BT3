### Title
Ankr Lock-Compensation Bypass via `MasterMagpie.withdraw()` — the `lockedAmount`/`unlockTime` check in `AnkrBNBPoolHelper` is never enforced when the receipt token is unstaked directly (`File: wombat/AnkrBNBPoolHelper.sol`, `rewards/MasterMagpie.sol`)

### Summary
`AnkrBNBPoolHelper` implements a one-year lock-compensation mechanism: users who receive a compensation deposit via `batchDepositLPFor` get an entry in `lockedAmount[user]`, and `AnkrBNBPoolHelper.withdraw()` is supposed to prevent them from reducing their staked balance below `lockedAmount[user]` before `unlockTime`. However, this enforcement lives entirely in the helper contract and is bypassable because the underlying staking position is a normal `MasterMagpie` pool position that any user can unstake directly through `MasterMagpie.withdraw(_stakingToken, _amount)`, which has no knowledge of, and never checks, `AnkrBNBPoolHelper.lockedAmount`.

### Finding Description
`AnkrBNBPoolHelper.withdraw()` enforces the lock: [1](#0-0) 

The lock check `if (unlockTime > block.timestamp && lockedAmount[msg.sender] > rest) revert NotAllowed();` only executes inside this specific function. The underlying position, however, is a standard stake of `stakingToken` (the Wombat receipt token) in `MasterMagpie`, credited via `depositFor`: [2](#0-1) 

`MasterMagpie` exposes a fully public, unprivileged `withdraw` entrypoint that lets any user unstake their own `stakingToken` balance directly, with no interaction with `AnkrBNBPoolHelper` at all: [3](#0-2) 

The internal `_withdraw`/`_harvestAndUnstake` logic only checks that the amount does not exceed the user's own `available` stake — it has no concept of `AnkrBNBPoolHelper.lockedAmount`: [4](#0-3) 

and transfers the raw `stakingToken` (the Wombat LP receipt token) straight to the caller: [5](#0-4) 

This is a structural analog of the referenced minimum-collateral bug: an enforcement check (minimum collateral / lock threshold) is implemented in only one code path (borrow-time check / `AnkrBNBPoolHelper.withdraw`), while a second, reachable, unprivileged path (collateral withdrawal / `MasterMagpie.withdraw`) allows the same underlying state change without the check ever running.

### Impact Explanation
A recipient of Ankr exploit compensation can call `MasterMagpie.withdraw(stakingToken, amount)` directly instead of going through `AnkrBNBPoolHelper.withdraw()`, unstaking any or all of their compensation position — including amounts that should remain locked under `lockedAmount[msg.sender]` until `unlockTime` — completely bypassing the lock. The user receives the liquid Wombat receipt token (`stakingToken`) in their wallet before `unlockTime`, which they can freely transfer or use elsewhere (e.g., re-stake, sell, or redeem via the pool's underlying mechanics), defeating the entire purpose of the one-year lock designed to compensate/restrict Ankr-affected users. This is a broken/bypassable access-control guarantee on user funds that the protocol explicitly built a dedicated contract to enforce.

### Likelihood Explanation
Likelihood is high: `MasterMagpie.withdraw` is a standard, always-available, unprivileged function with no special conditions or cost beyond normal gas, and the affected users already hold a stake in `stakingToken` from the moment they receive `batchDepositLPFor` compensation. No oracle, governance, or privileged role is needed — any holder of the locked position can trigger the bypass at will.

### Recommendation
Enforce the lock at the `MasterMagpie` layer for the affected staking token (e.g., track and check `lockedAmount`/`unlockTime` inside `MasterMagpie._withdraw`/`_harvestAndUnstake` for this pool, or mark the compensation stake as non-withdrawable through the generic `withdraw`/`withdrawFor` paths and only unlockable through a dedicated, lock-aware function), so that the restriction cannot be bypassed by calling `MasterMagpie` directly.

### Proof of Concept
1. `ankrOperator` calls `AnkrBNBPoolHelper.batchDepositLPFor(lpAmount, [user], [100000])`, which sets `lockedAmount[user] = amount` and stakes `amount` of `stakingToken` on the user's behalf in `MasterMagpie` via `depositFor` [6](#0-5) .
2. Before `unlockTime`, `user` calls `MasterMagpie.withdraw(stakingToken, amount)` directly (not `AnkrBNBPoolHelper.withdraw`) [3](#0-2) .
3. `MasterMagpie._harvestAndUnstake` only checks `user.available < _amount` and never references `AnkrBNBPoolHelper.lockedAmount`, so the withdrawal succeeds [7](#0-6) .
4. `stakingToken` (the Wombat receipt token) is transferred directly to `user` [5](#0-4) , fully unlocking the compensation position before `unlockTime`, with `AnkrBNBPoolHelper.lockedAmount[user]` left stale/unenforced.

### Citations

**File:** wombat/AnkrBNBPoolHelper.sol (L113-135)
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
    }    
```

**File:** wombat/AnkrBNBPoolHelper.sol (L158-177)
```text
    /// @notice withdraw stables from wombat pool, auto unstake from master Magpie
    /// @param _liquidity the amount of liquidity to withdraw
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

**File:** wombat/AnkrBNBPoolHelper.sol (L198-202)
```text
    /// @notice stake the receipt token in the masterchief of GMP on behalf of the caller
    function _stake(uint256 _amount, address _caller) internal {
        IERC20(stakingToken).safeApprove(masterMagpie, _amount);
        IMasterMagpie(masterMagpie).depositFor(stakingToken, _amount, _caller);
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
