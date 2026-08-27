### Title
Ankr compensation lock can be bypassed via `MasterMagpie.withdraw()` - ([File: rewards/MasterMagpie.sol])

### Summary
`AnkrBNBPoolHelper` enforces a time-locked minimum balance (`lockedAmount[msg.sender]`, unenforceable before `unlockTime`) only inside its own `withdraw()` function. Because the underlying staking accounting in `MasterMagpie` is fungible per `(stakingToken, account)` and the *generic* `deposit`/`withdraw` entry points on `MasterMagpie` are public and unrestricted (unlike `depositFor`/`withdrawFor`, which require `_onlyPoolHelper`), any user can unstake the same `stakingToken` balance directly from `MasterMagpie`, completely skipping the pool-helper's lock check — the exact "double spending"/bypass pattern described in the external report, where a balance meant to secure an obligation (there: loan health; here: a 1-year compensation lock) can be moved out from under the check that is supposed to protect it.

### Finding Description
`AnkrBNBPoolHelper` is used to distribute Ankr-exploit compensation. Compensation is staked on behalf of users via `_stake()` → `IMasterMagpie(masterMagpie).depositFor(stakingToken, amount, _caller)` [1](#0-0)  and a floor amount is recorded in `lockedAmount[_for[i]]` [2](#0-1) .

The intended lock is enforced only inside `AnkrBNBPoolHelper.withdraw()`:
```
uint256 rest = this.balance(msg.sender);
if (unlockTime > block.timestamp && lockedAmount[msg.sender] > rest) revert NotAllowed();
``` [3](#0-2) 

However, `MasterMagpie` tracks a user's staked `stakingToken` balance in a single fungible `userInfo[_stakingToken][_account]` record regardless of which caller deposited it [4](#0-3) . Crucially, `MasterMagpie` exposes public, unrestricted `deposit`/`withdraw` functions that operate on `msg.sender` directly:
```
function deposit(address _stakingToken, uint256 _amount) external whenNotPaused nonReentrant {
    _deposit(_stakingToken, msg.sender, _amount, false);
}
function withdraw(address _stakingToken, uint256 _amount) external whenNotPaused nonReentrant {
    _withdraw(_stakingToken, msg.sender, _amount, false);
}
``` [5](#0-4) 
These are separate from `depositFor`/`withdrawFor`, which are gated by `_onlyPoolHelper(_stakingToken)` [6](#0-5) , but the plain `withdraw()` has no such restriction and only checks `user.available < _amount` [7](#0-6) , then transfers the raw `stakingToken` (the compensation receipt token) straight to `msg.sender`:
```
IERC20(tokenToPoolInfo[_stakingToken].stakingToken).safeTransfer(address(msg.sender), _amount);
``` [8](#0-7) 

Because deposits performed via `depositFor` also increment `user.available` (the same field checked by the public `withdraw()`), [9](#0-8)  a recipient of locked Ankr compensation can simply call `MasterMagpie.withdraw(stakingToken, amount)` directly instead of `AnkrBNBPoolHelper.withdraw()`. This unstakes the receipt token into the user's wallet without ever evaluating `lockedAmount`/`unlockTime`, and without invoking `WombatStaking.burnReceiptToken()` — the safeguard is entirely bypassed.

### Impact Explanation
The `lockedAmount`/`unlockTime` mechanism is the sole guarantee that Ankr-exploit compensation remains staked for its intended duration. This bypass lets any recipient instantly withdraw the raw, freely-transferable receipt token to their own wallet ahead of `unlockTime`, defeating the compensation lock entirely and letting the token be transferred or otherwise disposed of immediately — mirroring the reported bug class where a balance meant to secure an obligation can be moved out from under the enforcement check tied to a specific contract path.

### Likelihood Explanation
Any address that received compensation via `batchDepositLPFor` can trigger this with a single unprivileged transaction calling the public `MasterMagpie.withdraw(stakingToken, amount)`; no special permissions, timing, or external conditions are required.

### Recommendation
Enforce the `lockedAmount`/`unlockTime` restriction inside `MasterMagpie` itself for the compensation `stakingToken` (e.g., by gating the generic `withdraw()`/checking a lock-aware hook), or make the Ankr compensation `stakingToken` pool only withdrawable via `withdrawFor` (restricted to the registered pool helper), so the lock cannot be bypassed by calling `MasterMagpie`'s public functions directly.

### Proof of Concept
1. `ankrOperator` calls `AnkrBNBPoolHelper.batchDepositLPFor(...)`, which sets `lockedAmount[user] = X` and stakes `X` of `stakingToken` for `user` via `MasterMagpie.depositFor` [10](#0-9) .
2. Before `unlockTime`, `user` calls `MasterMagpie.withdraw(stakingToken, X)` directly (not via `AnkrBNBPoolHelper.withdraw()`).
3. `MasterMagpie._withdraw` only checks `user.available >= X` (true, since the deposit set `available += X`), and transfers the raw `stakingToken` to `user` [8](#0-7) .
4. The `lockedAmount`/`unlockTime` check in `AnkrBNBPoolHelper.withdraw()` is never executed, and the compensation lock is fully bypassed.

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

**File:** wombat/AnkrBNBPoolHelper.sol (L198-202)
```text
    /// @notice stake the receipt token in the masterchief of GMP on behalf of the caller
    function _stake(uint256 _amount, address _caller) internal {
        IERC20(stakingToken).safeApprove(masterMagpie, _amount);
        IMasterMagpie(masterMagpie).depositFor(stakingToken, _amount, _caller);
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

**File:** rewards/MasterMagpie.sol (L352-370)
```text
    function depositFor(
        address _stakingToken,
        uint256 _amount,
        address _for
    ) external whenNotPaused _onlyPoolHelper(_stakingToken) nonReentrant {
        _deposit(_stakingToken, _for, _amount, false);
    }

    /// @notice Withdraw staking tokens from Mastser Magpie for a specific user. Can only be called by pool helper
    /// @param _stakingToken Staking token of the pool
    /// @param _amount amount to withdraw   
    /// @param _for address of the user to withdraw for, and also harvested reward will be sent to
    function withdrawFor(
        address _stakingToken,
        uint256 _amount,
        address _for
    ) external whenNotPaused _onlyPoolHelper(_stakingToken) nonReentrant {
        _withdraw(_stakingToken, _for, _amount, false);
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

**File:** rewards/MasterMagpie.sol (L507-522)
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
```
