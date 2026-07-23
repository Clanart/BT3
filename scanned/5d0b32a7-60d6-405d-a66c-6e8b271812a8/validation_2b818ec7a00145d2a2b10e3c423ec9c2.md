### Title
Excess ETH Sent to `exactOutputSingle`/`exactInputSingle` Is Permanently Claimable by Any Caller via Unguarded `refundETH()` — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`exactOutputSingle` and `exactInputSingle` are `payable` and support native ETH as WETH input. The internal `pay()` function wraps only the exact `amountIn` of ETH needed, leaving any `msg.value` surplus in the router. The three recovery helpers — `refundETH()`, `sweepToken()`, and `unwrapWETH9()` — carry no access control and accept caller-supplied `recipient` parameters. Any surplus ETH (or WETH) sitting in the router between transactions is therefore claimable by any third party, causing direct loss of user principal.

---

### Finding Description

**Step 1 — Surplus ETH enters the router.**

`exactOutputSingle` and `exactInputSingle` are both `payable`: [1](#0-0) [2](#0-1) 

Inside the swap callback, `pay()` is called with the exact pool-requested `amountIn`, not with `msg.value`: [3](#0-2) 

When `token == WETH` and `nativeBalance >= value`, the function wraps exactly `value` wei and forwards it to the pool. Any `msg.value - value` remainder stays in the router with no automatic refund.

**Step 2 — Recovery helpers have no access control.**

`refundETH()` sends the entire ETH balance to `msg.sender` — whoever calls it first: [4](#0-3) 

`sweepToken()` and `unwrapWETH9()` accept a caller-supplied `recipient` with no restriction: [5](#0-4) [6](#0-5) 

Any address — not the original depositor — can call these functions and redirect the full balance to themselves.

**Step 3 — The design requires multicall + `refundETH()`, but does not enforce it.**

The intended pattern is `multicall{value}([exactInputSingle(...), refundETH()])`, as shown in the test suite: [7](#0-6) 

However, `exactOutputSingle` is also called directly with ETH in tests (mixed native + WETH path), confirming it is a supported entry point: [8](#0-7) 

There is no on-chain enforcement that `msg.value` equals the actual `amountIn`, and no automatic refund at the end of either function.

---

### Impact Explanation

A user who calls `exactOutputSingle{value: X}` where `X > actual amountIn` loses `X - amountIn` ETH to any front-runner who calls `refundETH()` in the same block. The loss is exact and permanent: the original sender has no priority claim over the surplus. The same applies to WETH output left in the router when `recipient = address(router)` is used without a same-transaction `unwrapWETH9` or `sweepToken` call.

---

### Likelihood Explanation

- `exactOutputSingle` is `payable` and the mixed-ETH path is explicitly tested and documented as supported.
- Users quoting off-chain and sending a conservative `msg.value` overshoot (common UX pattern) will routinely leave surplus ETH.
- No on-chain guard, no NatSpec warning, and no automatic refund exist to prevent this.
- MEV bots routinely monitor router balances; any surplus is extracted within the same block.

---

### Recommendation

1. **Auto-refund surplus ETH** at the end of `exactOutputSingle` and `exactInputSingle` when `tokenIn == WETH`:
   ```solidity
   uint256 surplus = address(this).balance;
   if (surplus > 0) _transferETH(msg.sender, surplus);
   ```
2. **Restrict `refundETH()`** to send only to `msg.sender` within a multicall context, or record the original depositor in transient storage and enforce it.
3. **Add a `msg.sender` guard** to `sweepToken` and `unwrapWETH9` so that only the address that initiated the multicall can specify a recipient, analogous to how the callback context restricts the payer.

---

### Proof of Concept

```
1. Pool has bid/ask such that exactOutputSingle(amountOut=1500) costs amountIn=1000 wei WETH.

2. Alice calls:
     router.exactOutputSingle{value: 2000}(
       ExactOutputSingleParams({
         pool: pool,
         tokenIn: WETH,
         amountOut: 1500,
         amountInMaximum: 3000,
         ...
       })
     );

3. Inside metricOmmSwapCallback → _justPayCallback → pay(WETH, Alice, pool, 1000):
     nativeBalance = 2000 >= 1000
     WETH.deposit{value: 1000}()
     WETH.transfer(pool, 1000)
     // 1000 wei ETH remains in router

4. exactOutputSingle returns successfully. Alice's swap is complete.
   Router holds 1000 wei ETH.

5. Bob (front-runner, same block) calls:
     router.refundETH();
     // sends address(this).balance = 1000 to Bob

6. Alice lost 1000 wei ETH. Bob gained 1000 wei ETH.
   Alice cannot recover it; refundETH() has no access control.
```

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-130)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L37-45)
```text
  function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);

    if (balanceWETH > 0) {
      IWETH9(WETH).withdraw(balanceWETH);
      _transferETH(recipient, balanceWETH);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L48-55)
```text
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

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L72-104)
```text
  function test_mixedNativeAndWeth_exactOutputSingle_wethForToken() public {
    uint128 amountOut = 1_500;
    (uint256 quotedIn,) =
      quoter.quoteHypotheticalExactOutputSingle(address(pool), true, amountOut, 0, TEST_BID_X64, TEST_ASK_X64);
    uint256 nativePart = quotedIn / 2;
    uint256 wethPart = quotedIn - nativePart;

    uint256 token1Before = token1.balanceOf(recipient);
    uint256 swapperEthBefore = swapper.balance;
    uint256 swapperWethBefore = weth.balanceOf(swapper);

    vm.prank(swapper);
    uint256 amountIn = router.exactOutputSingle{value: nativePart}(
      IMetricOmmSimpleRouter.ExactOutputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountOut: amountOut,
        amountInMaximum: uint128(quotedIn * 2 + 1),
        recipient: recipient,
        deadline: _deadline(),
        priceLimitX64: 0,
        extensionData: ""
      })
    );

    assertEq(amountIn, quotedIn, "amountIn matches quote");
    assertEq(token1.balanceOf(recipient) - token1Before, amountOut, "exact token1 out");
    assertEq(swapperEthBefore - swapper.balance, nativePart, "swapper native spent");
    assertEq(swapperWethBefore - weth.balanceOf(swapper), wethPart, "swapper weth spent");
    _assertRouterEmpty();
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
