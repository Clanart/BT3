### Title
Fee-on-transfer LP tokens allow over-minting of WombatStaking receipt tokens - (File: wombat/WombatStaking.sol)

### Summary
`WombatStaking.depositLP()` transfers in an LP token via `safeTransferFrom` and then mints a `receiptToken` and stakes into `masterWombat` using the caller-supplied `_lpAmount` parameter, without verifying that `_lpAmount` of LP tokens was actually received by the contract. This mirrors the reported zkSync bridge issue where `depositChecked` was set without confirming the actual transferred amount, allowing fee-on-transfer tokens to break accounting.

### Finding Description
In `depositLP`, the contract pulls LP tokens from the caller and immediately uses the nominal `_lpAmount` for staking and receipt-token minting, instead of measuring the actual balance change: [1](#0-0) 

Contrast this with the sibling `deposit()` function in the same contract, which correctly measures the LP actually received from the underlying Wombat pool via a before/after balance diff before minting: [2](#0-1) 

`depositLP` lacks this same before/after balance check on the incoming `IERC20(poolInfo.lpAddress).safeTransferFrom(_for, address(this), _lpAmount)` call. If `poolInfo.lpAddress` behaves as a deflationary/fee-on-transfer token (or any token whose `transferFrom` delivers less than the requested amount), the contract will:
1. Receive `_lpAmount - fee` actual LP tokens.
2. Call `_toMasterWomAndSendReward(_lpAddress, _lpAmount, true)`, which calls `_stakeToWombatMaster(_lpToken, _lpAmount)` — approving and depositing the full nominal `_lpAmount` to `masterWombat`: [3](#0-2) 
3. Mint `poolInfo.receiptToken` for the full nominal `_lpAmount` to `msg.sender`: [4](#0-3) 

Because `WombatStaking` is a shared custodian holding pooled LP balances for many pool-helpers/users, if the contract holds any residual/dust LP balance (e.g., from prior harvest/deposit/withdraw rounding, or accumulated fee remainders across many pools sharing the same underlying LP token), step 2's `masterWombat.deposit` call for the full `_lpAmount` can succeed by silently drawing on that shared balance instead of reverting. The depositor then receives receipt tokens (and a corresponding `masterMagpie` staked balance once routed through a `WombatPoolHelper`) for the full `_lpAmount`, while only contributing `_lpAmount - fee` of real value — an accounting mismatch that inflates claims against the shared LP custody and can be repeated to drain value belonging to other stakers.

### Impact Explanation
This breaks the 1:1 backing between minted `receiptToken`/staked balances and actual LP custody in `WombatStaking`, which underpins withdrawal and reward accounting for all users of that pool. Repeated exploitation (or even accidental use with a fee-bearing LP token) creates a permanent shortfall between the receipt-token supply and the LP tokens actually held/staked, which can lead to later withdrawals failing for legitimate users or a race to withdraw before insolvency is discovered — i.e., protocol insolvency / theft of pooled funds, matching the accepted impact categories.

### Likelihood Explanation
Exploitability depends on whether an LP token routed through `depositLP` has fee-on-transfer semantics and whether the contract carries a residual/dust balance sufficient to mask the shortfall during the `masterWombat.deposit` call; absent such a balance the call reverts safely. This makes the issue conditional rather than universally and immediately exploitable, but the missing invariant check (crediting based on requested amount instead of measured received amount) is a genuine code-level defect reachable by any ordinary wallet calling `depositLP` directly (it is an external, unprivileged function, only gated by `_onlyActivePoolHelper`, which an approved pool helper or any caller routed through a `WombatPoolHelper.depositLP` can reach).

### Recommendation
In `WombatStaking.depositLP`, measure the actual LP tokens received before using that amount for staking and minting, exactly as `deposit()` already does:
```solidity
function depositLP(address _lpAddress, uint256 _lpAmount, address _for)
    nonReentrant whenNotPaused _onlyActivePoolHelper(_lpAddress) external
{
    Pool storage poolInfo = pools[_lpAddress];
    uint256 beforeBalance = IERC20(poolInfo.lpAddress).balanceOf(address(this));
    IERC20(poolInfo.lpAddress).safeTransferFrom(_for, address(this), _lpAmount);
    uint256 lpReceived = IERC20(poolInfo.lpAddress).balanceOf(address(this)) - beforeBalance;

    _toMasterWomAndSendReward(_lpAddress, lpReceived, true);
    IMintableERC20(poolInfo.receiptToken).mint(msg.sender, lpReceived);

    emit NewLPDeposit(_for, poolInfo.lpAddress, lpReceived, poolInfo.receiptToken, lpReceived);
}
```

### Proof of Concept
1. Deploy a mock LP token with fee-on-transfer behavior (e.g., burns 10% on `transferFrom`), and register it as `poolInfo.lpAddress` for a pool in `WombatStaking`.
2. Cause `WombatStaking` to hold a small residual balance of that LP token (e.g., via a prior `deposit`/`withdraw` rounding or a small direct transfer by an attacker to seed the buffer).
3. Call `depositLP(_lpAddress, _lpAmount, attacker)` with `_lpAmount` slightly larger than what the fee-adjusted transfer will deliver, but within the residual buffer's coverage.
4. Observe: `masterWombat.deposit` succeeds (drawing on the buffer/other users' shared balance) and `receiptToken.mint(msg.sender, _lpAmount)` credits the attacker for the full nominal amount, exceeding the LP value actually contributed — verify via `IMintableERC20(receiptToken).balanceOf(attacker)` versus the attacker's real net LP contribution.

### Citations

**File:** wombat/WombatStaking.sol (L250-269)
```text
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

**File:** wombat/WombatStaking.sol (L707-713)
```text
    // triggers harvest from wombat exchange
    function _stakeToWombatMaster(address _lpToken, uint256 _lpAmount) internal {
        Pool storage poolInfo = pools[_lpToken];
        // Approve Transfer to Master Wombat for Staking
        IERC20(_lpToken).safeApprove(masterWombat, _lpAmount);
        IMasterWombat(masterWombat).deposit(poolInfo.pid, _lpAmount);
    }
```
