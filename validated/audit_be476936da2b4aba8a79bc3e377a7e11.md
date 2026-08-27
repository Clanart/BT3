### Title
Bypass of AnkrBNBPoolHelper's `unlockTime`/`lockedAmount` withdrawal lock via direct `MasterMagpie.withdrawFor` call - ([File: wombat/AnkrBNBPoolHelper.sol])

### Summary
`AnkrBNBPoolHelper` enforces a time-lock (`unlockTime`) on batch-deposited "ankr compensation" LP positions only inside its own `withdraw()` entry point. The lock is checked *after* the position has already been unstaked from `MasterMagpie`, and it is never enforced by `MasterMagpie` itself. A holder of a locked position can call `MasterMagpie.withdrawFor(stakingToken, amount, msg.sender)` directly, exactly the same way the Nextcloud "Secure View" restriction (enforced only on the public-share page) was bypassed by hitting the underlying `/download` route directly.

### Finding Description
`AnkrBNBPoolHelper.batchDepositLPFor` records a per-user `lockedAmount` and relies on the immutable `unlockTime` to prevent withdrawal before the compensation lock expires: [1](#0-0) 

The lock is checked only inside this contract's own `withdraw()` function, and only *after* the receipt token has already been unstaked from `MasterMagpie` via `_unstake`: [2](#0-1) 

`_unstake` simply forwards to `MasterMagpie.withdrawFor`: [3](#0-2) 

`MasterMagpie`'s own doc comment concedes that the generic `...For()` functions (used here) are only "supposed to be called by other contract[s] designed by Magpie's team" rather than being cryptographically/access-restricted like the VLMGP/mWomSV-specific wrappers, which explicitly use `_onlyVlMgp()`/`_onlyMWomSV()` modifiers: [4](#0-3) [5](#0-4) 

Because the lock/unlock restriction (`unlockTime > block.timestamp && lockedAmount[msg.sender] > rest`) lives only in `AnkrBNBPoolHelper.withdraw`, and `MasterMagpie.withdrawFor` provides an equivalent unstake path that is not gated by the same check, a locked user can call `withdrawFor(stakingToken, amount, msg.sender)` on `MasterMagpie` directly (specifying themselves as `_account`) to pull the mintable/transferable receipt token (`IMintableERC20` per `WombatStaking.depositLP`) out of the pool into their own wallet, completely skipping the ankr lock check that the helper contract was designed to enforce.

### Impact Explanation
This nullifies the intended one-year lock on ankr-compensation deposits: the receipt token obtained via the bypass is a standard transferable ERC20 (minted/burned by `WombatStaking` per `IMintableERC20(poolInfo.receiptToken).mint(...)`), so a user can move or sell this position well before the mandated `unlockTime`, defeating the entire purpose of the lock. This is a premature/forfeited-lock class issue — the restriction meant to freeze funds for a defined period can be trivially circumvented.

### Likelihood Explanation
No privileged role is required. Any address that received a batch-locked ankr deposit (`lockedAmount[_for[i]] > 0`) can call the public `MasterMagpie.withdrawFor` function directly instead of going through `AnkrBNBPoolHelper.withdraw`, entirely from an unprivileged wallet, in a single transaction.

### Recommendation
Enforce the `unlockTime`/`lockedAmount` check inside `MasterMagpie` itself for this pool's staking token (e.g., via a pool-specific hook checked on every withdrawal path), or restrict `withdrawFor`/`depositFor` so that only the registered `helper` for a given `stakingToken` pool may invoke them on behalf of a user, mirroring the `_onlyVlMgp`/`_onlyMWomSV` pattern already used elsewhere in `MasterMagpie`.

### Proof of Concept
1. `ankrOperator` calls `AnkrBNBPoolHelper.batchDepositLPFor` for victim/attacker address `A`, setting `lockedAmount[A] = X` and `unlockTime = T` (future).
2. Before `T`, `A` calls `MasterMagpie.withdrawFor(stakingToken, X, A)` directly (bypassing `AnkrBNBPoolHelper.withdraw`'s lock check entirely).
3. `A` receives the transferable receipt token in their own wallet per `_withdraw`'s `safeTransfer` and can immediately transfer/sell it, despite `unlockTime` not having passed — the lock is effectively unenforced.

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

**File:** wombat/AnkrBNBPoolHelper.sol (L204-207)
```text
    /// @notice unstake from the masterchief of GMP on behalf of the caller
    function _unstake(uint256 _amount, address _sender) internal {
        IMasterMagpie(masterMagpie).withdrawFor(stakingToken, _amount, _sender);
    }
```

**File:** rewards/MasterMagpie.sol (L29-33)
```text
/// @title A contract for managing all reward pools
/// @author Magpie Team
/// @notice You can use this contract for depositing MGP, MWOM, and Liquidity Pool tokens.
/// @dev All the ___For() function are function which are supposed to be called by other contract designed by Magpie's team

```

**File:** rewards/MasterMagpie.sol (L451-477)
```text
    function depositVlMGPFor(
        uint256 _amount,
        address _for
    ) external whenNotPaused _onlyVlMgp() {
        _deposit(address(vlmgp), _for, _amount, true);
    }
    
    function withdrawVlMGPFor(
        uint256 _amount,
        address _for
    ) external whenNotPaused _onlyVlMgp() {
        _withdraw(address(vlmgp), _for, _amount, true);
    }

    function depositMWomSVFor(
        uint256 _amount,
        address _for
    ) external whenNotPaused _onlyMWomSV() {
        _deposit(address(mWomSV), _for, _amount, true);
    }
    
    function withdrawMWomSVFor(
        uint256 _amount,
        address _for
    ) external whenNotPaused _onlyMWomSV() {
        _withdraw(address(mWomSV), _for, _amount, true);
    }    
```
