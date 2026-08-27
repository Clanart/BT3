### Title
`WombatPoolHelperV2::depositFor` hardcodes zero minimum liquidity, allowing sandwich attacks to steal depositor funds - (File: `wombat/WombatPoolHelperV2.sol`)

### Summary
`WombatPoolHelperV2::depositFor` (lines 103-107) forwards user-deposited funds into the underlying Wombat pool with `_minimumLiquidity` hardcoded to `0`, removing any slippage protection for this entry point, unlike the sibling `deposit()` function which lets the caller pass their own `_minimumLiquidity`. This mirrors the H-6 Arrakis finding: "No slippage control on ... deposit can cause unlimited loss."

### Finding Description
`WombatPoolHelperV2::depositFor` is a fully permissionless external function callable by any wallet:

```solidity
function depositFor(uint256 _amount, address _for) external {
    IERC20(depositToken).safeTransferFrom(msg.sender, address(this), _amount);
    IERC20(depositToken).safeApprove(wombatStaking, _amount);
    _deposit(_amount, 0, _for, address(this));
}
``` [1](#0-0) 

It calls the internal `_deposit` helper with a fixed `_minimumLiquidity` of `0`: [2](#0-1) 

This flows through to `WombatStaking::deposit`, which forwards the same `_minimumLiquidity` straight into the underlying Wombat pool's `deposit(...)` call: [3](#0-2) 

The Wombat pool computes the LP/liability amount to mint based on the current `cash`/`liability` ratio of the underlying asset (the stableswap invariant), exactly as in the mock implementation used for testing: [4](#0-3) 

Because `_minimumLiquidity` is forced to `0` in `depositFor`, there is no way for the depositor (or the entity acting `_for` them) to bound how few LP/receipt tokens they receive relative to the value of stablecoins deposited. An attacker can, exactly as described in the referenced Arrakis report, swap heavily against the target Wombat pool immediately before the victim's `depositFor` transaction to skew the `cash`/`liability` ratio unfavorably, then reverse the swap immediately after, extracting value from the victim's deposit through the sandwich. The `deposit()` function is not vulnerable since it exposes `_minimumLiquidity` to the caller, but `depositFor` silently strips that protection.

### Impact Explanation
Any unprivileged wallet using `depositFor` (or any caller depositing `_for` another user, e.g. a compounding/zap flow) can have its deposited stablecoins converted into an amount of LP/receipt tokens far below fair value, resulting in direct, quantifiable theft of user funds extracted by the sandwiching attacker. This satisfies "concrete direct theft of user funds."

### Likelihood Explanation
The attack requires only the ability to submit two ordinary swap transactions against the target Wombat pool surrounding the victim's `depositFor` call — no privileged role, oracle manipulation, or governance action is needed. Given `depositFor` is public and reachable by any wallet, and MEV/front-running infrastructure is commonly available, likelihood is high whenever the pool has meaningful liquidity depth compared to the deposit size.

### Recommendation
Expose a user-supplied `_minimumLiquidity` parameter on `depositFor` (as is already done on `deposit`) instead of hardcoding `0`, and revert if the underlying Wombat pool mints less than that threshold.

### Proof of Concept
1. Victim calls `WombatPoolHelperV2::depositFor(amount, victim)` intending to deposit `amount` of `depositToken`.
2. Attacker front-runs with a large swap on the underlying Wombat pool that skews the `cash`/`liability` ratio for `depositToken`'s asset unfavorably.
3. Victim's transaction executes: `WombatStaking::deposit` calls the pool's `deposit` with `_minimumLiquidity = 0`, so it succeeds regardless of how few LP/receipt tokens are minted per [5](#0-4) .
4. Attacker back-runs, reversing the initial swap and pocketing the difference, mirroring the DAI/USDC scenario in the H-6 report.

### Citations

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

**File:** mocks/wombat/WombatPoolMock.sol (L36-81)
```text
    function deposit(
        address token,
        uint256 amount,
        uint256 minimumLiquidity,
        address to,
        uint256 deadline,
        bool shouldStake
    ) external returns (uint256 liquidity) {
        IERC20(token).safeTransferFrom(address(msg.sender), address(this), amount);

        IWombatAssetMock lpToken = IWombatAssetMock(depositTokenToLp[token]);

        depositBalance[msg.sender] += amount;

        uint256 liabilityToMint = exactDepositLiquidityInEquilImpl(
            int256(amount),
            int256(uint256(lpToken.cash())),
            int256(uint256(lpToken.liability())),
            int256(ampFactor)
        ).toUint256();

        if (liabilityToMint < amount) {
            liabilityToMint = amount;
        }

        uint256 lpTokenToMint = (
            lpToken.liability() == 0
                ? liabilityToMint
                : (liabilityToMint * lpToken.totalSupply()) / lpToken.liability()
        );
        
        if (!shouldStake) {
            lpToken.mint(to, lpTokenToMint);
        } else {
            lpToken.mint(address(this), lpTokenToMint);

            lpToken.approve(address(masterWombat), lpTokenToMint);

            uint256 pid = masterWombat.getAssetPid(address(lpToken)); 
            masterWombat.depositFor(pid, lpTokenToMint, to);
        }

        lpToken.addCash(amount);
        lpToken.addLiability(liabilityToMint);

        return amount;
```
