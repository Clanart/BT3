### Title
Payable swap and liquidity entry-points silently trap `msg.value` when `tokenIn` is not WETH or when excess ETH is sent, enabling front-runner theft via `refundETH()` - (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

Every swap and liquidity function on `MetricOmmSimpleRouter` and `MetricOmmPoolLiquidityAdder` is `payable`, but the internal `pay()` helper in `PeripheryPayments` only consumes native ETH when `token == WETH`. When the input token is any other ERC-20, all `msg.value` is silently left in the contract. When the token is WETH but `msg.value > amountIn`, only `amountIn` is wrapped and the surplus stays. Because `refundETH()` unconditionally forwards the entire contract balance to `msg.sender`, any ETH stranded between transactions can be stolen by an unprivileged caller.

---

### Finding Description

`PeripheryPayments.pay()` branches on `token == WETH`:

```solidity
// PeripheryPayments.sol L69-88
function pay(address token, address payer, address recipient, uint256 value) internal {
    if (payer == address(this)) {
        IERC20(token).safeTransfer(recipient, value);
    } else if (token == WETH) {
        uint256 nativeBalance = address(this).balance;
        if (nativeBalance >= value) {
            IWETH9(WETH).deposit{value: value}();   // wraps exactly `value`, not `nativeBalance`
            IERC20(WETH).safeTransfer(recipient, value);
        } else if (nativeBalance > 0) { ... }
        else { IERC20(WETH).safeTransferFrom(payer, recipient, value); }
    } else {
        IERC20(token).safeTransferFrom(payer, recipient, value); // ETH completely ignored
    }
}
``` [1](#0-0) 

Two distinct loss paths exist:

**Path A – non-WETH token, any `msg.value > 0`:** The `else` branch calls `safeTransferFrom` and never touches `address(this).balance`. All ETH sent with the call is ignored and remains in the contract.

**Path B – WETH token, `msg.value > amountIn`:** The branch wraps exactly `value` (the pool-requested amount), leaving `nativeBalance - value` ETH stranded.

None of the four payable entry-points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) contain a guard of the form `require(msg.value == 0)` for non-WETH tokens or `require(msg.value == amountIn)` for WETH tokens. [2](#0-1) 

The same applies to `addLiquidityExactShares` and `addLiquidityWeighted` in `MetricOmmPoolLiquidityAdder`, which are also `payable` and delegate payment to the same `pay()` helper. [3](#0-2) 

The stranded ETH is then exposed to theft through `refundETH()`:

```solidity
// PeripheryPayments.sol L58-63
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);   // sends to caller, not original depositor
    }
}
``` [4](#0-3) 

`refundETH()` is unpermissioned and sends the **entire** contract balance to whoever calls it. Any ETH left in the contract between transactions (i.e., when the user does not include `refundETH()` in the same `multicall` batch) is claimable by any address.

---

### Impact Explanation

**Direct loss of user principal.** A user who sends ETH alongside a non-WETH swap (Path A) loses 100 % of that ETH unless they include `refundETH()` in the same `multicall`. A user who over-sends ETH for a WETH swap (Path B) loses the surplus. In both cases a front-runner monitoring the mempool can call `refundETH()` in the same block and redirect the stranded ETH to themselves. The loss is bounded only by the amount the victim sent; there is no protocol-side cap.

---

### Likelihood Explanation

- All four swap functions and both liquidity functions are `payable`, making it natural for users and integrators to send ETH.
- The WETH-as-native-ETH pattern (send ETH, router wraps it) is standard in Uniswap-style periphery; users familiar with that pattern will routinely send ETH even for non-WETH tokens or send a rounded-up amount.
- `multicall` batching is the intended usage pattern; a user who omits `refundETH()` from the batch silently loses funds.
- No on-chain guard or revert prevents the erroneous call, so the error is invisible at execution time.

---

### Recommendation

1. **In `exactInputSingle` / `exactOutputSingle`:** add a guard before the swap:
   ```solidity
   if (params.tokenIn != WETH) {
       require(msg.value == 0, "ETH not accepted for ERC20 input");
   } else {
       require(msg.value == params.amountIn, "msg.value must equal amountIn");
   }
   ```
2. **In `exactInput` / `exactOutput`:** apply the same check against `params.tokens[0]` and `params.amountIn`.
3. **In `MetricOmmPoolLiquidityAdder`:** add analogous guards in `addLiquidityExactShares` and `addLiquidityWeighted` for both pool tokens.
4. **Alternatively**, make `refundETH()` send to a caller-supplied `recipient` parameter (not `msg.sender`) so that only the original depositor can recover stranded ETH, reducing the theft surface even if ETH is accidentally trapped.

---

### Proof of Concept

**Scenario (Path A – non-WETH token):**

1. Alice calls `exactInputSingle` with `tokenIn = USDC`, `amountIn = 1000e6`, and accidentally attaches `msg.value = 1 ether`.
2. The pool callback fires; `pay(USDC, Alice, pool, 1000e6)` hits the `else` branch and calls `USDC.safeTransferFrom(Alice, pool, 1000e6)`. The 1 ETH is never touched.
3. The swap completes successfully; Alice receives her output tokens. Her 1 ETH sits in the router.
4. Bob observes the transaction in the mempool (or after inclusion) and calls `refundETH()`.
5. `refundETH()` sends `address(this).balance` (Alice's 1 ETH) to Bob. Alice's ETH is permanently lost.

**Scenario (Path B – WETH token, excess ETH):**

1. Alice calls `exactInputSingle` with `tokenIn = WETH`, `amountIn = 0.5 ether`, and sends `msg.value = 1 ether`.
2. `pay(WETH, Alice, pool, 0.5e18)` sees `nativeBalance = 1 ether >= value = 0.5 ether`, wraps exactly 0.5 ETH, and transfers it to the pool. The remaining 0.5 ETH stays in the router.
3. Bob calls `refundETH()` and receives Alice's 0.5 ETH. [5](#0-4) [6](#0-5)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-81)
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

  /// @notice Add liquidity with explicit per-bin shares for `msg.sender`.
  function addLiquidityExactShares(
    address pool,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateDeltas(deltas);
    return _addLiquidity(pool, msg.sender, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```
