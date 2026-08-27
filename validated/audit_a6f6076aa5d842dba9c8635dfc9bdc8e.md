## Title
Hardcoded Zero Slippage in `WombatPoolHelperV2.depositFor` Enables Sandwich-Attack Theft of User Deposits - (File: `wombat/WombatPoolHelperV2.sol`)

### Summary
`WombatPoolHelperV2.depositFor()` hardcodes the `_minimumLiquidity` slippage parameter to `0` when depositing a user's tokens into the underlying Wombat pool, unlike the sibling `deposit()` function which lets the caller supply their own minimum. This removes all slippage protection for any user (or integrator) calling `depositFor`, allowing an attacker to sandwich the deposit and steal value from the depositor.

### Finding Description
`deposit()` correctly forwards a caller-supplied `_minimumLiquidity` to `_deposit`: [1](#0-0) 

However `depositFor()` ignores this and hardcodes `0`: [2](#0-1) 

This `0` is passed straight through `_deposit` into `WombatStaking.deposit`, which forwards it unmodified as the Wombat pool's `minimumLiquidity` check: [3](#0-2) 

Because `minimumLiquidity` is the Wombat AMM's slippage guard on the deposit's LP output, hardcoding it to `0` means the deposit will succeed no matter how unfavorable the exchange rate is at execution time — there is no protection against price movement between transaction submission and execution.

### Impact Explanation
`depositFor` is a fully public, unprivileged entry point (no access control) that any wallet can call for itself or on behalf of another address, transferring `depositToken` from `msg.sender` and depositing it into the Wombat pool on the `_for` address's behalf. Because slippage protection is hardcoded to zero, an attacker can:
1. Front-run the victim's `depositFor` call by manipulating the Wombat pool's internal exchange rate (e.g., via a large swap that moves `cash`/`liability` for the deposited asset), which depresses the effective LP/receipt tokens minted for a given deposit amount.
2. Let the victim's deposit execute at the manipulated (unfavorable) rate, since `minimumLiquidity == 0` accepts any output.
3. Back-run to restore the pool state and capture the difference in value that the victim overpaid for their LP/receipt tokens.

This is a direct theft of user funds during deposit, since the victim receives fewer receipt tokens (and thus a smaller staked share, and less redeemable underlying value) than the fair-market amount for their deposited tokens.

### Likelihood Explanation
This is not an admin-driven or governance-related issue — it is directly reachable by any ordinary wallet calling `depositFor` (or interacting with an integrator/router that calls it), which is core, everyday user functionality for staking into the Wombat pool via `WombatPoolHelperV2`. Wombat pools are stableswap-style AMMs whose `cash`/`liability` ratios can be shifted by ordinary trades, making the sandwich economically feasible whenever pool depth is not excessive relative to trade size. The bug is a straightforward hardcoded-value defect (matching the "hardcoded slippage" bug class), requiring no special conditions beyond mempool visibility of the `depositFor` transaction.

### Recommendation
Add a `_minimumLiquidity` parameter to `depositFor` (mirroring `deposit`) so the caller can supply a slippage bound appropriate to current market conditions, and forward it to `_deposit` instead of the hardcoded `0`.

### Proof of Concept
1. Attacker observes a pending `depositFor(amount, victim)` transaction in the mempool.
2. Attacker swaps a large amount of `depositToken` (or the paired asset) into the same Wombat pool to skew `cash`/`liability`, degrading the LP/receipt tokens a subsequent deposit of `depositToken` would yield.
3. Victim's `depositFor` transaction executes; since `_minimumLiquidity` is hardcoded to `0` in [2](#0-1) , the call succeeds despite the unfavorable rate, minting fewer receipt/staking tokens to the victim than fair value.
4. Attacker reverses their initial swap, restoring the pool and pocketing the value difference extracted from the victim's deposit.

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

**File:** wombat/WombatStaking.sol (L242-264)
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

```
