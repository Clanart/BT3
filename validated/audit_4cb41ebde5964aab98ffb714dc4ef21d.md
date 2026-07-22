### Title
ETH Sent to Payable Swap/Liquidity Functions With Non-WETH `tokenIn` Is Silently Trapped and Stealable — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

Every swap entry-point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) and every liquidity-adder entry-point is declared `payable`. When `tokenIn` is not WETH, `PeripheryPayments.pay()` ignores `address(this).balance` entirely and falls through to `safeTransferFrom`. Any ETH attached to the call is silently stranded in the router. Because `refundETH()` is a public function that sends the full contract ETH balance to whoever calls it first, a third party (e.g., an MEV bot) can immediately drain the stranded ETH.

---

### Finding Description

`PeripheryPayments.pay()` has three branches: [1](#0-0) 

```
Branch 1 (payer == address(this)):  ERC-20 transfer from router
Branch 2 (token == WETH):           wrap native ETH first, pull remainder from payer
Branch 3 (else):                    safeTransferFrom(payer, recipient, value)
                                    ← address(this).balance is NEVER read here
```

When a user calls any payable swap function with `tokenIn != WETH` and `msg.value > 0`, the ETH is accepted (the function is `payable`) but `pay()` falls into Branch 3 and never touches `address(this).balance`. The ETH remains in the router after the swap completes. [2](#0-1) 

`refundETH()` is a public, permissionless function that sends the entire ETH balance of the router to `msg.sender`: [3](#0-2) 

Any address can call it immediately after the victim's transaction, stealing the stranded ETH. The same vulnerability exists in `MetricOmmPoolLiquidityAdder`, which is also `payable` and uses the same `pay()` helper: [4](#0-3) 

A secondary variant exists even for WETH swaps: when `msg.value > amountIn`, Branch 2 wraps exactly `value` (the swap amount) and leaves the surplus ETH (`msg.value - amountIn`) in the router, also stealable via `refundETH()`. [5](#0-4) 

---

### Impact Explanation

Direct loss of user ETH equal to `msg.value`. The ETH is not returned to the user and can be stolen by any third party who calls `refundETH()`. For the non-WETH case, 100% of the attached ETH is lost. For the WETH-with-excess case, the surplus above `amountIn` is lost. The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) does not protect against this because it only applies to plain ETH transfers, not to `msg.value` attached to function calls. [6](#0-5) 

---

### Likelihood Explanation

Medium. A user may accidentally send ETH when swapping non-WETH tokens due to a frontend bug, copy-paste error, or confusion about the interface. The attack is trivially executable by any MEV bot monitoring the mempool: the bot sees the victim's transaction, waits for it to be included, then immediately calls `refundETH()` in the next block (or front-runs with a higher gas price in the same block). No special permissions or setup are required.

---

### Recommendation

1. **Reject ETH for non-WETH swaps**: At the start of each payable swap/liquidity function, add:
   ```solidity
   if (params.tokenIn != WETH && msg.value > 0) revert ETHNotAccepted();
   ```
2. **Reject excess ETH for WETH swaps**: For WETH swaps, add:
   ```solidity
   if (msg.value > params.amountIn) revert ExcessETH(msg.value, params.amountIn);
   ```
3. **Or make non-WETH functions non-payable**: Remove `payable` from functions that do not need to accept ETH, so the EVM itself reverts on any attached value.

---

### Proof of Concept

**Non-WETH case (100% ETH loss):**

```
1. Alice calls exactInputSingle{value: 1 ether}(params)
   where params.tokenIn = USDC (not WETH), params.amountIn = 1000 USDC

2. Inside metricOmmSwapCallback → _justPayCallback → pay(USDC, Alice, pool, 1000)
   → token != WETH → else branch → safeTransferFrom(Alice, pool, 1000)
   → address(this).balance (1 ether) is never touched

3. Swap completes. Router holds 1 ether.

4. Bob (MEV bot) calls refundETH() → receives 1 ether.

5. Alice loses 1 ether.
```

**WETH-with-excess case (partial ETH loss):**

```
1. Alice calls exactInputSingle{value: 2 ether}(params)
   where params.tokenIn = WETH, params.amountIn = 1 ether

2. pay(WETH, Alice, pool, 1 ether):
   nativeBalance (2 ether) >= value (1 ether)
   → deposit{value: 1 ether}() → transfer 1 WETH to pool
   → 1 ether surplus remains in router

3. Bob calls refundETH() → receives 1 ether surplus.

4. Alice loses 1 ether.
``` [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L192-199)
```text
  function _justPayCallback(int256 amount0Delta, int256 amount1Delta) private {
    pay(
      _getTokenToPay(),
      _getPayer(),
      msg.sender,
      uint256(MetricOmmSwapResults.extractPositiveAmount(amount0Delta, amount1Delta))
    );
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
