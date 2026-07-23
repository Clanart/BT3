### Title
Payable Functions Accept ETH Without Consuming It for Non-WETH Operations, Enabling ETH Theft via `refundETH()` — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`, `metric-periphery/contracts/MetricOmmSimpleRouter.sol`, `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

Multiple `payable` functions across the periphery layer accept ETH but never consume it when the operation does not involve WETH. The internal `pay()` function uses `address(this).balance` (the entire contract ETH balance) rather than tracking per-call `msg.value`. The `refundETH()` function, which has no access control, sends the entire contract ETH balance to `msg.sender`. Together, these create a cross-user ETH theft vector: ETH deposited by one user during a non-WETH operation is permanently accessible to any third party who calls `refundETH()`, or is silently consumed to subsidize another user's WETH swap.

---

### Finding Description

`PeripheryPayments.sol` defines the internal `pay()` function used by both `MetricOmmSimpleRouter` and `MetricOmmPoolLiquidityAdder` to settle token obligations:

```solidity
function pay(address token, address payer, address recipient, uint256 value) internal {
    if (payer == address(this)) {
        IERC20(token).safeTransfer(recipient, value);
    } else if (token == WETH) {
        uint256 nativeBalance = address(this).balance;   // ← uses entire contract balance
        if (nativeBalance >= value) {
            IWETH9(WETH).deposit{value: value}();
            IERC20(WETH).safeTransfer(recipient, value);
        } else if (nativeBalance > 0) {
            IWETH9(WETH).deposit{value: nativeBalance}();
            IERC20(WETH).safeTransfer(recipient, nativeBalance);
            IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
        } else {
            IERC20(WETH).safeTransferFrom(payer, recipient, value);
        }
    } else {
        IERC20(token).safeTransferFrom(payer, recipient, value);   // ← ETH ignored
    }
}
``` [1](#0-0) 

The `receive()` guard only blocks direct ETH transfers from non-WETH addresses:

```solidity
receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
}
``` [2](#0-1) 

However, `receive()` is **not invoked** when a `payable` function is called with ETH. The following functions are all `payable` and accept ETH unconditionally:

- `exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput` in `MetricOmmSimpleRouter`
- `addLiquidityExactShares` (both overloads) and `addLiquidityWeighted` (both overloads) in `MetricOmmPoolLiquidityAdder`
- `sweepToken`, `unwrapWETH9`, `refundETH` in `PeripheryPayments`
- `selfPermit`, `selfPermitIfNecessary`, `selfPermitAllowed`, `selfPermitAllowedIfNecessary` in `SelfPermit`
- `multicall` in both contracts [3](#0-2) [4](#0-3) [5](#0-4) 

When any of these functions is called with ETH and the operation does not involve WETH (i.e., `token != WETH` in `pay()`), the ETH is silently accepted and left in `address(this).balance` without being used or tracked.

`refundETH()` has no access control and sends the **entire** contract ETH balance to `msg.sender`:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
``` [6](#0-5) 

This means any ETH deposited by User A (even accidentally) is immediately claimable by any User B who calls `refundETH()`.

A second impact vector exists because `pay()` uses `address(this).balance` (not `msg.value`) for WETH payments. If ETH from a prior user is sitting in the contract, a subsequent caller of `exactInputSingle(tokenIn=WETH)` with 0 ETH sent will have their swap obligation paid by the prior user's stuck ETH, effectively stealing it. [7](#0-6) 

---

### Impact Explanation

**Direct loss of user ETH principal.** Any ETH sent with a non-WETH `payable` call (e.g., `addLiquidityExactShares` on a DAI/USDC pool, `sweepToken`, `selfPermit`) is permanently accessible to any third party via `refundETH()`. Additionally, excess ETH from a WETH swap (user sends more than the required amount) is similarly exposed. In both cases, the original depositor loses their ETH with no recourse.

---

### Likelihood Explanation

- All swap and liquidity functions are `payable`, which signals to users and integrators that ETH is accepted. Users adding liquidity to a WETH pool may send ETH; if the pool is non-WETH, the ETH is silently trapped.
- Integrators composing `multicall` batches may send ETH for one leg and accidentally leave residual ETH after the WETH leg consumes only part of it.
- `selfPermit*` functions are `payable` but never use ETH under any circumstances; ETH sent with them is always wasted.
- The attack to drain stuck ETH via `refundETH()` requires zero privilege and is a single external call.

---

### Recommendation

1. **Remove `payable` from functions that never use ETH**: `sweepToken`, `unwrapWETH9`, `selfPermit`, `selfPermitIfNecessary`, `selfPermitAllowed`, `selfPermitAllowedIfNecessary`. These are designed for `multicall` composition but do not themselves consume ETH; removing `payable` prevents accidental ETH deposits.

2. **Track `msg.value` per call rather than using `address(this).balance`**: In `pay()`, replace `address(this).balance` with a parameter or transient slot that records only the ETH contributed by the current top-level call. This prevents cross-user ETH consumption.

3. **Restrict `refundETH()` to return ETH only to the original depositor**, or document clearly that it is a cleanup function that must be called in the same `multicall` batch as the ETH-consuming operation.

---

### Proof of Concept

**Scenario A — ETH theft via `refundETH()`:**

1. User A calls `addLiquidityExactShares(pool=DAI_USDC_POOL, ...)` and accidentally sends 1 ETH (the function is `payable`, so it is accepted without revert).
2. Inside `metricOmmModifyLiquidityCallback`, `pay(DAI, userA, pool, amount0)` is called. Since `DAI != WETH`, the branch `IERC20(DAI).safeTransferFrom(userA, pool, amount0)` executes. The 1 ETH is never touched.
3. User B observes the contract's ETH balance (1 ETH) on-chain and calls `refundETH()`.
4. `refundETH()` sends `address(this).balance` (1 ETH) to User B. User A's ETH is permanently lost.

**Scenario B — Cross-user ETH subsidy:**

1. User A calls `exactInputSingle(tokenIn=WETH, amountIn=0.5 ETH)` and sends 1 ETH. The swap uses 0.5 ETH; 0.5 ETH remains in the contract.
2. User B calls `exactInputSingle(tokenIn=WETH, amountIn=0.5 ETH)` with 0 ETH sent.
3. In the swap callback, `pay(WETH, userB, pool, 0.5 ETH)` is called. `address(this).balance == 0.5 ETH` (User A's residual). The branch `nativeBalance >= value` is taken; User A's 0.5 ETH is wrapped and sent to the pool on behalf of User B.
4. User B receives swap output without paying. User A's 0.5 ETH is consumed. [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L58-63)
```text
  function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L69-88)
```text
  function pay(address token, address payer, address recipient, uint256 value) internal {
    // If the payer is contract it means we are in the middle of a path. In the middle of a path we operate on ERC20 only.
    if (payer == address(this)) {
      IERC20(token).safeTransfer(recipient, value);
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
      } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
      }
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/contracts/base/SelfPermit.sol (L15-17)
```text
  function selfPermit(address token, uint256 value, uint256 deadline, uint8 v, bytes32 r, bytes32 s) public payable {
    IERC20Permit(token).permit(msg.sender, address(this), value, deadline, v, r, s);
  }
```
