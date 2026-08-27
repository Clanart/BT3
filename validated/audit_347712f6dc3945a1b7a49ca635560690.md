### Title
MasterMagpie emission accounting can be diluted by direct-transfer donation to the staking-token balance used as the reward-per-share denominator - (File: rewards/MasterMagpie.sol)

### Summary
`MasterMagpie` computes `accMGPPerShare` for every pool using `_calLpSupply(_stakingToken)`, which for ordinary staking-token pools simply returns the raw ERC20 `balanceOf(address(this))` of that staking token rather than the sum of tracked `UserInfo.amount` values. Any unprivileged wallet can inflate this denominator by directly transferring staking/receipt tokens to the `MasterMagpie` contract without calling `deposit`, permanently diluting the MGP emission rate credited to legitimate stakers.

### Finding Description
`updatePool` and `_calMGPReward` derive the emission rate per staking-token unit from `lpSupply = _calLpSupply(_stakingToken)`: [1](#0-0) 

`_calLpSupply` uses `IERC20(_stakingToken).balanceOf(address(this))` as the denominator for all pools other than `vlmgp`/`mWomSV` (which use `totalSupply()` of those specific lock tokens instead): [2](#0-1) 

Because `accMGPPerShare` accumulates `mgpReward * 1e12 / lpSupply`, and `lpSupply` is a raw token balance rather than the sum of registered `UserInfo.amount`, any wallet can call a plain ERC20 `transfer()` of the pool's staking/receipt token directly to the `MasterMagpie` contract address (bypassing `deposit`/`depositFor`) to inflate `lpSupply` without creating any corresponding `UserInfo` entry. This is the same class of root cause as the reported "first depositor" bug: unaudited raw-balance/totalSupply relationships used as a share/rate denominator that can be manipulated by direct token donation to the contract, distinct from the officially tracked deposit accounting (`user.amount`).

The deposit/withdraw functions confirm that the `stakingToken` balance held by `MasterMagpie` is not reconciled against the sum of `user.amount`; deposits/withdrawals move exact amounts, and there's no assertion that `balanceOf(address(this)) == sum(user.amount)`: [3](#0-2) 

Once the donated tokens land in the contract, they inflate `lpSupply` for every future `updatePool` call, but they are never credited to any `UserInfo`, so nobody can withdraw them and they permanently sit in the denominator, diluting `accMGPPerShare` growth for all real stakers of that pool indefinitely (until/unless removed by an admin action, which is out of scope for an ordinary wallet to trigger or reverse).

### Impact Explanation
MGP is pre-funded/transferred out of the contract as pool-share-weighted rewards (`_sendMGP`, `_sendMGPForVlMGPPool`, `_sendVlMGPFor`). If `lpSupply` used in `accMGPPerShare` calculation is inflated by a direct donation, the amount of MGP credited to genuine stakers per unit time is permanently reduced relative to the correct emission — the "lost" share of MGP emission is never credited to any user and is not recoverable, constituting a permanent loss/freeze of legitimate stakers' unclaimed yield. This matches the accepted impact category "theft or permanent freezing of unclaimed yield."

### Likelihood Explanation
The attack requires no privileged role: any wallet holding (or acquiring, e.g. by depositing then transferring) some amount of a pool's staking/receipt token can simply call `transfer()` to the `MasterMagpie` contract address at any time, including immediately after a new pool is registered via `registerPool`, when `lpSupply` is near zero and the dilution effect is most severe and cheapest to execute.

### Recommendation
Do not derive `lpSupply` (and therefore `accMGPPerShare`) from the raw `balanceOf(address(this))` of the staking token. Instead, track and use an internal `poolTotalStaked` accumulator that is only updated inside `_deposit`/`_withdraw` (mirroring the sum of `UserInfo.amount`), consistent with how `vlmgp`/`mWomSV` pools already use `totalSupply()` rather than a token balance. This removes the ability for direct ERC20 transfers to affect emission-rate math.

### Proof of Concept
1. Admin registers a new pool via `registerPool` for a receipt token (`stakingToken`), which starts with `lpSupply = 0`.
2. Attacker acquires some amount of that receipt token (e.g., by making a legitimate small deposit through `WombatPoolHelper`/`WombatStaking` to receive receipt tokens) and immediately calls `receiptToken.transfer(masterMagpieAddress, largeAmount)` directly — bypassing `MasterMagpie.deposit`.
3. `IERC20(_stakingToken).balanceOf(address(this))` (used inside `_calLpSupply`) is now inflated, while no corresponding `UserInfo.amount` exists for the donated amount.
4. Real users then call `deposit`/`depositFor`; every subsequent `updatePool` call computes `accMGPPerShare` using the inflated `lpSupply`, permanently reducing the MGP-per-share rate credited to actual stakers for the lifetime of the pool.
5. The donated tokens can never be withdrawn by anyone (no `UserInfo` entry references them), and the diluted MGP emission is never made up elsewhere, permanently freezing/losing that portion of yield for legitimate stakers. [1](#0-0) [2](#0-1)

### Citations

**File:** rewards/MasterMagpie.sol (L372-396)
```text
    /// @notice Update reward variables of the given pool to be up-to-date.
    /// @param _stakingToken Staking token of the pool
    function updatePool(address _stakingToken) public whenNotPaused {
        PoolInfo storage pool = tokenToPoolInfo[_stakingToken];
        if (block.timestamp <= pool.lastRewardTimestamp || totalAllocPoint == 0) {
            return;
        }
        uint256 lpSupply = _calLpSupply(_stakingToken);
        if (lpSupply == 0) {
            pool.lastRewardTimestamp = block.timestamp;
            return;
        }        
        uint256 multiplier = block.timestamp - pool.lastRewardTimestamp;
        uint256 mgpReward = (multiplier * mgpPerSec * pool.allocPoint) / totalAllocPoint;
        
        pool.accMGPPerShare = pool.accMGPPerShare + ((mgpReward * 1e12) / lpSupply);
        pool.lastRewardTimestamp = block.timestamp;

        emit UpdatePool(
            _stakingToken,
            pool.lastRewardTimestamp,
            lpSupply,
            pool.accMGPPerShare
        );
    }    
```

**File:** rewards/MasterMagpie.sol (L482-514)
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
```

**File:** rewards/MasterMagpie.sol (L659-667)
```text
    function _calLpSupply(address _stakingToken) internal view returns (uint256) {
        if (_stakingToken == address(vlmgp)) {
            return IERC20(address(vlmgp)).totalSupply();
        }
        if (_stakingToken == address(mWomSV)) {
            return IERC20(address(mWomSV)).totalSupply();
        }
        return IERC20(_stakingToken).balanceOf(address(this));
    }
```
