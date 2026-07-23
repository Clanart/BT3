### Title
Unattributed Router-Held Native ETH Consumed by Any WETH Swap Caller, Enabling Theft of Stranded ETH — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` uses `address(this).balance` — the router's **total** native ETH balance — to cover any external caller's WETH payment obligation. Because this balance carries no per-user attribution, ETH stranded on the router from one user's transaction is silently consumed by the next WETH swap caller, or can be directly claimed by anyone via the unguarded `refundETH()`.

---

### Finding Description

In `pay()`, when `token == WETH` and `payer != address(this)`, the function reads the router's entire native balance and uses it to settle the current caller's WETH obligation:

```solidity
// PeripheryPayments.sol lines 73-84
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // ← total router ETH, not msg.value
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

`nativeBalance` is the router's **aggregate** ETH balance — it includes ETH sent by the current caller **and** any ETH stranded from prior transactions. There is no per-user accounting. When `nativeBalance >= value`, the router deposits that ETH as WETH and transfers it to the pool, never pulling from the payer's own WETH balance.

ETH arrives on the router via `payable` swap functions (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, `multicall`). The intended pattern is that users include `refundETH()` as the last step of their `multicall` to reclaim excess ETH. When they omit it, the ETH is stranded.

`refundETH()` is public with no access control and sends the router's **entire** ETH balance to `msg.sender`:

```solidity
// PeripheryPayments.sol lines 58-63
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
``` [2](#0-1) 

Similarly, `sweepToken()` and `unwrapWETH9()` are public with no access control and transfer the router's full token/WETH balance to any caller-chosen recipient. [3](#0-2) 

The `receive()` guard only blocks direct ETH pushes; it does not prevent ETH from arriving via `payable` function calls. [4](#0-3) 

---

### Impact Explanation

**Direct loss of user ETH principal — High severity.**

Two concrete theft paths exist:

**Path 1 — Free WETH swap using victim's ETH:**
Attacker calls `exactInputSingle(tokenIn=WETH, amountIn=X)`. Inside `_justPayCallback`, `pay(WETH, attacker, pool, X)` is invoked. [5](#0-4) 

If `address(this).balance >= X`, the router deposits the victim's stranded ETH as WETH and transfers it to the pool. The attacker's own WETH is never touched — they receive the full swap output at zero cost.

**Path 2 — Direct ETH theft via `refundETH()`:**
Attacker calls `refundETH()` and receives the victim's entire stranded ETH balance in one call.

In both cases the victim loses their ETH principal with no recourse.

---

### Likelihood Explanation

**Medium.** The test suite explicitly demonstrates the pattern that creates stranded ETH:

```solidity
// test: multicall{value: 2 ether} with amountIn=1_000 and refundETH as step 2
// If step 2 is omitted, 2 ether - 1_000 wei remains on the router
``` [6](#0-5) 

Any user who:
- sends `msg.value > amountIn` to a `payable` swap function without a trailing `refundETH()`, or
- calls `multicall{value: X}` and omits `refundETH()` as the final step

leaves ETH on the router. Front-running bots monitoring the mempool can detect such transactions and immediately call `refundETH()` or execute a zero-cost WETH swap in the next block.

---

### Recommendation

1. **Track per-transaction ETH**: Store `msg.value` in transient storage at the start of each `payable` entry point and limit `pay()`'s native ETH consumption to that amount. Clear it at the end of the call.
2. **Restrict `refundETH()` to the original depositor**: Record the depositor address in transient storage and enforce it in `refundETH()`.
3. **Alternatively**, enforce that `pay()` only uses native ETH equal to the current transaction's `msg.value` by passing it explicitly rather than reading `address(this).balance`.

---

### Proof of Concept

```
Setup:
  - Router deployed with WETH and factory
  - Pool seeded with WETH/token1 liquidity
  - Victim has 1 ETH

Step 1 — Victim strands ETH:
  victim calls multicall{value: 1 ETH}([
      exactInputSingle(tokenIn=WETH, amountIn=500, ...)
      // refundETH() omitted
  ])
  → pay() uses 500 wei of the router's 1 ETH balance
  → 1 ETH - 500 wei remains on the router

Step 2a — Attacker steals via refundETH():
  attacker calls router.refundETH()
  → attacker receives (1 ETH - 500 wei) directly
  → victim's ETH is gone

Step 2b — Attacker steals via free WETH swap:
  attacker calls exactInputSingle(tokenIn=WETH, amountIn=500, ...)
  → pay(WETH, attacker, pool, 500) is called
  → address(this).balance = (1 ETH - 500 wei) >= 500
  → router deposits victim's ETH as WETH, transfers to pool
  → attacker receives token1 output without spending any WETH
``` [7](#0-6) [8](#0-7)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L37-55)
```text
  function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);

    if (balanceWETH > 0) {
      IWETH9(WETH).withdraw(balanceWETH);
      _transferETH(recipient, balanceWETH);
    }
  }

  /// @inheritdoc IPeripheryPayments
  function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceToken = IERC20(token).balanceOf(address(this));
    if (balanceToken < amountMinimum) revert InsufficientToken(token, amountMinimum, balanceToken);

    if (balanceToken > 0) {
      IERC20(token).safeTransfer(recipient, balanceToken);
    }
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

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L106-133)
```text
  function test_multicall_ethInput_exactInputSingle_refundsUnusedEth() public {
    uint128 amountIn = 1_000;
    uint256 msgValue = 2 ether;
    uint256 swapperEthBefore = swapper.balance;

    vm.prank(swapper);
    bytes[] memory calls = new bytes[](2);
    calls[0] = abi.encodeWithSelector(
      router.exactInputSingle.selector,
      IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: amountIn,
        amountOutMinimum: 0,
        recipient: recipient,
        deadline: _deadline(),
        priceLimitX64: 0,
        extensionData: ""
      })
    );
    calls[1] = abi.encodeWithSelector(router.refundETH.selector);
    router.multicall{value: msgValue}(calls);

    assertEq(swapper.balance, swapperEthBefore - amountIn, "unused eth refunded");
    _assertRouterEmpty();
  }
```
