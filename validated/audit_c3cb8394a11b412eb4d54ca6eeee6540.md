### Title
Ankr 1-Year Lock Compensation Can Be Bypassed via `MasterMagpie.withdraw()` + Transferable Receipt Token - (File: wombat/AnkrBNBPoolHelper.sol)

### Summary
`AnkrBNBPoolHelper` enforces a time-lock (`unlockTime`) on compensation shares distributed via `batchDepositLPFor`, tracked per-address in `lockedAmount`. This lock is checked only inside `AnkrBNBPoolHelper.withdraw()`, based on `msg.sender`'s current balance in the shared `BaseRewardPool`/`MasterMagpie` accounting. Exactly like the reported `Pool.sol` bug where the vesting check on `owner` could be bypassed by moving the vault's transferable ERC-20 shares to another address, here the lock check can be bypassed because the underlying staking position can be extracted from `MasterMagpie` and moved to a fresh, unlocked address before the real withdrawal happens.

### Finding Description
`AnkrBNBPoolHelper.withdraw()` is the only place enforcing the lock: [1](#0-0) 

The check compares `lockedAmount[msg.sender]` (set once at compensation time in `batchDepositLPFor`) against the caller's *current* balance in the reward pool: [2](#0-1) [3](#0-2) 

Critically, `MasterMagpie` exposes a completely unrestricted `deposit`/`withdraw` pair that any wallet can call directly for any registered `stakingToken`, with no helper gating (unlike `depositFor`/`withdrawFor`, which are gated by `_onlyPoolHelper`): [4](#0-3) [5](#0-4) 

This lets a locked ankr user pull the raw `stakingToken` (the transferable receipt token minted by `WombatStaking`) straight out of `MasterMagpie` to their own EOA, bypassing `AnkrBNBPoolHelper` entirely (`lockedAmount` is never consulted by `MasterMagpie.withdraw`). Since the receipt token is a standard transferable ERC-20 (`IMintableERC20`, minted in `WombatStaking.deposit`/`depositLP`): [6](#0-5) 

the user can then transfer this token to a fresh, never-locked address. That fresh address has `lockedAmount == 0`, so when it calls `AnkrBNBPoolHelper.depositLP()` followed immediately by `AnkrBNBPoolHelper.withdraw()`, the guard `lockedAmount[msg.sender] > rest` is trivially false and the underlying LP/stablecoin is redeemed from Wombat before `unlockTime`, fully circumventing the intended 1-year lock — the same root cause as the reported analog: a vesting/lock check keyed on an address whose position can be relocated via a transferable token.

### Impact Explanation
This defeats the entire purpose of the Ankr compensation lock (`unlockTime`), allowing affected users to redeem "locked" compensation funds immediately instead of waiting the intended period. This is a direct, unauthorized early release of funds that were designed to remain locked/vested, undermining the protocol's compensation guarantees.

### Likelihood Explanation
The path only requires standard unprivileged calls: `MasterMagpie.withdraw`, an ERC-20 `transfer`, `AnkrBNBPoolHelper.depositLP`, and `AnkrBNBPoolHelper.withdraw`. No privileged role, oracle, or governance action is needed — any wallet holding ankr compensation shares can execute it unilaterally.

### Recommendation
Enforce the lock check at the `MasterMagpie` accounting layer (e.g., by tracking `lockedAmount`/`unlockTime` directly in `MasterMagpie`'s `UserInfo`, or by preventing the generic `deposit`/`withdraw` functions from being usable for `stakingToken`s that have an associated lock), rather than relying solely on a check performed in an optional wrapper contract (`AnkrBNBPoolHelper`) that can be trivially routed around.

### Proof of Concept
1. Ankr-affected user `A` receives compensation via `batchDepositLPFor`, setting `lockedAmount[A] = X` and staking `X` of `stakingToken` into `MasterMagpie` on `A`'s behalf (`wombat/AnkrBNBPoolHelper.sol` lines 113-135).
2. Before `unlockTime`, `A` calls `MasterMagpie.withdraw(stakingToken, X)` directly (no helper gate) — this transfers the raw receipt token back to `A`'s wallet, with no reference to `lockedAmount` (`rewards/MasterMagpie.sol` lines 344-346, 508-514).
3. `A` transfers the receipt token to a fresh address `B` (`lockedAmount[B] == 0`).
4. `B` calls `AnkrBNBPoolHelper.depositLP(X)` to re-stake, then immediately calls `AnkrBNBPoolHelper.withdraw(X, minAmount)`.
5. In `withdraw()`, `rest = balance(B) = 0` after unstake, and `lockedAmount[B] == 0`, so `lockedAmount[msg.sender] > rest` is false — the check never reverts even though `block.timestamp < unlockTime`, and `B` redeems the full underlying stablecoin/LP amount, completing the bypass of the 1-year lock.

### Citations

**File:** wombat/AnkrBNBPoolHelper.sol (L88-94)
```text
    
    /// @notice get the total amount of shares of a user
    /// @param _address the user
    /// @return the amount of shares
    function balance(address _address) external override view returns (uint256) {
        return IBaseRewardPool(rewarder).balanceOf(_address);
    }
```

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

**File:** rewards/MasterMagpie.sol (L337-346)
```text
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

**File:** rewards/MasterMagpie.sol (L508-514)
```text
    function _withdraw(address _stakingToken, address _account, uint256 _amount, bool _isVlMgp) internal {
        _harvestAndUnstake(_stakingToken, _account, _amount, _isVlMgp);

        if (!_isVlMgp)
            IERC20(tokenToPoolInfo[_stakingToken].stakingToken).safeTransfer(address(msg.sender), _amount);
        emit Withdraw(_account, _stakingToken, _amount);
    }
```

**File:** wombat/WombatStaking.sol (L272-287)
```text
    function depositLP(
        address _lpAddress,
        uint256 _lpAmount,
        address _for
    ) nonReentrant whenNotPaused _onlyActivePoolHelper(_lpAddress) external {
        // Get information of the Pool of the token
        Pool storage poolInfo = pools[_lpAddress];

        // Transfer lp to this contract and stake it to wombat
        IERC20(poolInfo.lpAddress).safeTransferFrom(_for, address(this), _lpAmount);

        _toMasterWomAndSendReward(_lpAddress, _lpAmount, true); // triggers harvest from wombat exchange
        IMintableERC20(poolInfo.receiptToken).mint(msg.sender, _lpAmount);

        emit NewLPDeposit(_for, poolInfo.lpAddress, _lpAmount, poolInfo.receiptToken, _lpAmount);
    }
```
