### Title
Blacklisted users permanently lose access to withdrawn stablecoin liquidity in `WombatStaking.withdraw()` - (File: `wombat/WombatStaking.sol`)

### Summary
`WombatStaking.withdraw()` sends the underlying deposit token (e.g. USDC/USDT-style stablecoins used in Wombat stable pools) directly to `msg.sender` of the caller-chain (the withdrawing user), with no ability to redirect the transfer to an alternate address. If that user's address becomes blacklisted on the deposit token contract, this transfer permanently reverts, and the entire withdrawal transaction (including unstaking from `MasterMagpie` and burning the receipt token) is rolled back, permanently locking the user's LP position.

### Finding Description
Users deposit stable liquidity into Wombat pools via `WombatPoolHelper`/`WombatPoolHelperV2`/`AnkrBNBPoolHelper`, which forward the call to `WombatStaking.deposit`/`depositLP` and auto-stake the resulting receipt token into `MasterMagpie` on the user's behalf [1](#0-0) .

To exit, the user calls the pool helper's `withdraw()`, which is a single atomic flow: it calls `IWombatStaking(wombatStaking).withdraw(lpToken, _liquidity, _minAmount, msg.sender)`, then unstakes from `MasterMagpie` via `_unstake(_liquidity, msg.sender)`, then burns the receipt token [2](#0-1) .

Inside `WombatStaking.withdraw()`, after withdrawing liquidity from the Wombat AMM into the contract, the deposit token is sent directly to `_sender` (which is always `msg.sender` of the original caller, hardcoded by the pool helper, with no override parameter):
```solidity
IERC20(poolInfo.depositToken).safeTransfer(
    _sender,
    IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw
);
``` [3](#0-2) 

`poolInfo.depositToken` is admin-configured per pool and, since Wombat is a stableswap protocol, is expected to be a stablecoin such as USDC/USDT, which support address blacklisting by their issuers. `WombatStaking.withdraw()` is restricted to being called only by the registered pool helper (`_onlyPoolHelper`), and the pool helper itself provides no parameter to specify an alternate recipient — the recipient is always tied to `msg.sender` of the withdrawing account. The user's staked position is tracked internally in `MasterMagpie`'s `userInfo[stakingToken][account]` mapping keyed to the account address [4](#0-3) , and the receipt token itself is held/burned by the pool helper contract rather than distributed to the user's wallet as a freely transferable balance, so the user cannot route around the block by transferring a receipt-token balance to a fresh, non-blacklisted address.

Consequently, if the deposit token issuer blacklists a user's address (an external, non-privileged/non-admin-protocol event, identical in nature to the original report), that user's entire liquidity position becomes permanently unwithdrawable through the protocol's own contracts: `safeTransfer` to a blacklisted address reverts, and the reversion causes the whole `withdraw()` transaction (unstake + burn + transfer) to fail every time it is attempted.

### Impact Explanation
This results in permanent freezing of a legitimate user's staked/deposited stablecoin funds, with no recovery path available anywhere in the reachable contract logic (no alternate-recipient parameter, no transferable receipt token escape hatch). This matches the "permanent freezing of funds" impact criterion.

### Likelihood Explanation
Likelihood depends on external stablecoin issuers blacklisting a specific address, which is outside the protocol's control, but is a real, previously-abused capability of USDC/USDT-class tokens. Given the protocol explicitly targets deployment on chains (Optimism, Polygon, per the original analog) where such stablecoins are the natural `depositToken` choice for stable-swap pools, this is a realistic, non-privileged, ordinary-user-triggered scenario.

### Recommendation
Add a pull-based fallback (e.g., escrow the deposit token and let the user claim it later, or allow the withdrawer to specify a delivery address that can be trusted/verified separately, such as recovering to a designated recovery address if the direct transfer fails) rather than performing the token transfer atomically in the same call that also unstakes and burns the receipt token. At minimum, decouple the token transfer from the unstake/burn steps using a try/catch and internal accounting so a failed transfer doesn't lock up the entire unstake operation.

### Proof of Concept
1. Admin configures a Wombat pool in `WombatStaking` with `depositToken` = a blacklistable stablecoin (e.g., USDC on Optimism).
2. Alice deposits stable liquidity through `WombatPoolHelper.deposit()`, which stakes her receipt token into `MasterMagpie` under `userInfo[stakingToken][Alice]`.
3. USDC issuer blacklists Alice's address for unrelated reasons.
4. Alice calls `WombatPoolHelper.withdraw(_liquidity, _minAmount)`.
5. Internally, `WombatStaking.withdraw()` withdraws liquidity from the Wombat pool and attempts `IERC20(depositToken).safeTransfer(Alice, amount)`, which reverts because Alice is blacklisted [5](#0-4) .
6. The whole transaction reverts, so Alice's `MasterMagpie` stake is never reduced and the receipt token is never burned — she is left permanently unable to withdraw her stablecoin liquidity through this protocol.

### Citations

**File:** wombat/WombatPoolHelper.sol (L123-140)
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
        //  last burn the staking token withdrawn from Master Magpie
        IWombatStaking(wombatStaking).burnReceiptToken(lpToken, _liquidity);


        emit NewWithdraw(msg.sender, _liquidity);
    }
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

**File:** wombat/WombatStaking.sol (L295-321)
```text
    function withdraw(
        address _lpToken,
        uint256 _liquidity,
        uint256 _minAmount,
        address _sender
    ) nonReentrant whenNotPaused _onlyPoolHelper(_lpToken) external {
        Pool storage poolInfo = pools[_lpToken];

        IERC20(poolInfo.lpAddress).safeApprove(poolInfo.depositTarget, _liquidity);
        _toMasterWomAndSendReward(_lpToken, _liquidity, false);

        uint256 beforeWithdraw = IERC20(poolInfo.depositToken).balanceOf(address(this));
        IWombatPool(poolInfo.depositTarget).withdraw(
            poolInfo.depositToken,
            _liquidity,
            _minAmount,
            address(this),
            block.timestamp
        );

        IERC20(poolInfo.depositToken).safeTransfer(
            _sender,
            IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw
        );

        emit NewWithdraw(_sender, poolInfo.depositToken, _liquidity);
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
