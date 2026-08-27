### Title
`WombatPoolHelperV2.depositFor` hardcodes `_minimumLiquidity` to 0, exposing depositors to 100% slippage / MEV sandwich - (File: wombat/WombatPoolHelperV2.sol)

### Summary
`WombatPoolHelperV2.depositFor` ignores any caller-chosen slippage protection and forwards a hardcoded `0` as `_minimumLiquidity` to `WombatStaking.deposit`, which in turn passes it straight to the underlying Wombat pool's `deposit`. This is the same bug class as the reported `AaveUtils._tokenSwapOutAmount` issue: an output/slippage bound that can be zero, letting a swap/deposit clear at an arbitrarily bad rate and be sandwiched by an MEV bot for profit.

### Finding Description
`depositFor` is a public, unprivileged entry point that pulls `depositToken` from the caller and deposits it into the Wombat pool on behalf of `_for`: [1](#0-0) 

Unlike the regular `deposit(uint256 _amount, uint256 _minimumLiquidity)` entry point, which lets the caller supply their own minimum-liquidity slippage bound: [2](#0-1) 

`depositFor` unconditionally hardcodes `_minimumLiquidity = 0` when calling `_deposit`, which then calls `IWombatStaking(wombatStaking).deposit(lpToken, _amount, _minimumLiquidity, _for, _from)`: [3](#0-2) 

That value flows unchanged into the Wombat pool's `deposit` call inside `WombatStaking.deposit`, meaning the actual AMM-side slippage check is effectively disabled for anyone using `depositFor`: [4](#0-3) 

An attacker can front-run a `depositFor` call by manipulating the Wombat pool's cash/liability ratio for the target asset (e.g., via a large swap or lopsided deposit/withdraw), causing the pool to mint far fewer LP tokens than fair value for the victim's deposited amount, then back-run to restore the pool state and capture the difference — with zero possibility of the deposit reverting since the minimum is 0.

### Impact Explanation
Because the LP tokens minted directly determine the receipt/staking tokens minted to the victim (`stakingToken` balance delta is staked 1:1), any slippage suffered here is a permanent, direct loss of the depositor's principal value to the attacker performing the sandwich — this is direct theft of user funds, matching the report's core concern about unchecked zero minOut enabling 100% slippage extraction.

### Likelihood Explanation
`depositFor` is a normal, unprivileged external function usable by any wallet (or any integrator calling on a user's behalf) with no way to opt into slippage protection. Any Wombat pool experiencing a cash/liability imbalance (which is a normal, frequently occurring AMM condition and can also be actively induced by an attacker within a single block) makes every `depositFor` call in that block sandwichable, and MEV infrastructure for exactly this pattern (sandwiching zero-min-out calls) is common and cheap to run.

### Recommendation
Add a `_minimumLiquidity` parameter to `depositFor` so callers can supply their own slippage bound (as `deposit` already does), or compute an on-chain reasonable minimum (e.g., based on a quoted `quotePotentialDeposit`) and enforce it, ensuring the value passed to `WombatStaking.deposit` is never hardcoded to `0`.

### Proof of Concept
1. Attacker monitors mempool for a pending `depositFor(_amount, _for)` call on `WombatPoolHelperV2`.
2. Attacker front-runs with a swap/deposit that skews the target pool's cash/liability ratio unfavorably for the deposit token, depressing the LP tokens the pool would mint for a given deposit amount.
3. Victim's `depositFor` transaction executes with `_minimumLiquidity = 0` (hardcoded), so `WombatPool.deposit` succeeds despite minting significantly fewer LP tokens than fair value — there is no revert path.
4. Attacker back-runs to restore the pool ratio and/or withdraw, capturing the value difference that the victim lost, exactly analogous to the referenced `_tokenSwapOutAmount` returning 0 and enabling a 0 `_minOut` swap.

### Citations

**File:** wombat/WombatPoolHelperV2.sol (L98-101)
```text
    /// @param _amount the amount of stables to deposit
    function deposit(uint256 _amount, uint256 _minimumLiquidity) external override {
        _deposit(_amount, _minimumLiquidity, msg.sender, msg.sender);
    }
```

**File:** wombat/WombatPoolHelperV2.sol (L103-107)
```text
    function depositFor(uint256 _amount, address _for) external {
        IERC20(depositToken).safeTransferFrom(msg.sender, address(this), _amount);
        IERC20(depositToken).safeApprove(wombatStaking, _amount);
        _deposit(_amount, 0, _for, address(this));
    }    
```

**File:** wombat/WombatPoolHelperV2.sol (L155-162)
```text
    function _deposit(uint256 _amount, uint256 _minimumLiquidity, address _for, address _from) internal {
        uint256 beforeDeposit = IERC20(stakingToken).balanceOf(address(this));
        IWombatStaking(wombatStaking).deposit(lpToken, _amount, _minimumLiquidity, _for, _from);
        uint256 afterDeposit = IERC20(stakingToken).balanceOf(address(this));
        _stake(afterDeposit - beforeDeposit, _for);
        
        emit NewDeposit(_for, _amount);
    }
```

**File:** wombat/WombatStaking.sol (L242-269)
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
```
