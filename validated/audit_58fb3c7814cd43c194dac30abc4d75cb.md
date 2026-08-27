### Title
`WombatPoolHelperV2.depositFor` hardcodes zero minimum liquidity, exposing depositors to sandwich-attack theft - (File: wombat/WombatPoolHelperV2.sol)

### Summary
`WombatPoolHelperV2.depositFor(uint256 _amount, address _for)` forwards a hardcoded `0` as the `_minimumLiquidity` slippage parameter to `WombatStaking.deposit`, which in turn passes it straight through to the underlying Wombat `IWombatPool.deposit` call. This removes all slippage/sandwich protection for any caller of this unprivileged, permissionless function, unlike the sibling `deposit()` and `depositNative()` functions in the very same contract, which correctly accept a caller-supplied `_minimumLiquidity`.

### Finding Description
`depositFor` is defined as: [1](#0-0) 

It calls the internal `_deposit` helper with `_minimumLiquidity` fixed at `0`: [2](#0-1) 

`_deposit` forwards this value unmodified into `IWombatStaking(wombatStaking).deposit(lpToken, _amount, _minimumLiquidity, _for, _from)`. Inside `WombatStaking.deposit`, this parameter is passed as-is to the Wombat pool's `deposit` call: [3](#0-2) 

`IWombatPool.deposit`'s `minimumLiquidity` argument is the only guard against receiving fewer LP shares than expected due to price/invariant movement between transaction submission and execution: [4](#0-3) 

By contrast, the contract's own `deposit()` function correctly exposes this as a caller-controlled parameter: [5](#0-4) 

This is the same bug class as the referenced report — an on-chain liquidity/deposit operation executed with a forced zero slippage bound — except here the zero bound removes protection entirely (rather than causing reverts), silently allowing manipulated pricing to reduce the depositor's minted LP/receipt tokens.

### Impact Explanation
`depositFor` is callable by any unprivileged wallet for any `_for` address, and transfers `_amount` of `depositToken` from `msg.sender`. Because `_minimumLiquidity` is forced to `0`, an attacker can manipulate the underlying Wombat pool's cash/liability ratio immediately before the victim's `depositFor` transaction executes (e.g., via a large swap or deposit in the same block) to depress the LP-per-token mint rate, then reverse the manipulation immediately after. The victim's deposit will still succeed (no minimum enforced) but mint fewer receipt/LP tokens than fair value, and the attacker captures the difference. This is a direct extraction of value from an ordinary user's deposited funds — a concrete theft of user funds via MEV/sandwich attack, not merely a griefing or gas-only issue.

### Likelihood Explanation
Wombat pools (StableSwap-style with dynamic cash/liability ratios) are sensitive to just such manipulation, and MEV searchers routinely monitor mempools for exactly this kind of unprotected liquidity operation. `depositFor` has no access control and can be triggered by anyone depositing on behalf of any address, so any third party (or even the depositor's own front-run bot) can exploit it whenever a `depositFor` transaction is visible in the mempool.

### Recommendation
Add a `_minimumLiquidity` parameter to `depositFor` (mirroring `deposit()`/`depositNative()`) and forward the caller-supplied value instead of hardcoding `0`:
```solidity
function depositFor(uint256 _amount, uint256 _minimumLiquidity, address _for) external {
    IERC20(depositToken).safeTransferFrom(msg.sender, address(this), _amount);
    IERC20(depositToken).safeApprove(wombatStaking, _amount);
    _deposit(_amount, _minimumLiquidity, _for, address(this));
}
```

### Proof of Concept
1. Attacker observes a pending `depositFor(amount, for)` transaction on `WombatPoolHelperV2` in the mempool.
2. Attacker front-runs with a large swap/deposit on the same Wombat pool asset, temporarily skewing the cash/liability ratio so that the pool's `deposit` mint formula yields a lower liability-to-LP conversion rate.
3. Victim's `depositFor` executes with `_minimumLiquidity = 0`, so it succeeds despite receiving a below-market amount of LP/receipt tokens — no revert occurs because there is no floor to violate.
4. Attacker back-runs, reversing their swap/deposit and restoring the pool ratio, realizing a profit equal to the value extracted from the victim's deposit.

### Citations

**File:** wombat/WombatPoolHelperV2.sol (L99-101)
```text
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

**File:** wombat/WombatStaking.sol (L256-266)
```text
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
```

**File:** interfaces/wombat/IWombatPool.sol (L17-24)
```text
    function deposit(
        address token,
        uint256 amount,
        uint256 minimumLiquidity,
        address to,
        uint256 deadline,
        bool shouldStake
    ) external returns (uint256 liquidity);
```
