### Title
Stranded `msg.value` ETH on Router Consumed by Subsequent User's WETH Payment, Causing Cross-Transaction ETH Theft — (`File: metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` reads `address(this).balance` — the router's **total** ETH balance — when deciding how much native ETH to wrap toward a WETH payment. Because the router is shared across all callers and transactions, any ETH left on the router from a prior user's excess `msg.value` is silently consumed by the next user's WETH payment, transferring the prior user's ETH to the pool on behalf of the next user and causing a direct, unrecoverable loss of the prior user's principal.

---

### Finding Description

`PeripheryPayments.pay()` handles WETH payments with the following logic: [1](#0-0) 

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // ← total router ETH, not msg.value
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);  // ← pulls from payer
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
```

The variable `nativeBalance` is `address(this).balance`, which is the **entire** ETH balance of the router contract, not the ETH contributed by the current caller's `msg.value`. ETH can accumulate on the router between transactions when a user calls a payable entry point (e.g., `exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`, `addLiquidityExactShares`, `addLiquidityWeighted`) with excess `msg.value` and omits `refundETH()` from their multicall. [2](#0-1) 

The `receive()` guard only blocks direct ETH pushes from non-WETH addresses; it does not prevent `msg.value` from accumulating across separate transactions: [3](#0-2) 

The protocol's own tests acknowledge that users **must** include `refundETH()` in a multicall to recover unused ETH: [4](#0-3) 

When a user forgets this step, the surplus ETH persists on the router and is available to any subsequent caller.

The same `pay()` function is shared by both `MetricOmmSimpleRouter` and `MetricOmmPoolLiquidityAdder`: [5](#0-4) 

---

### Impact Explanation

**Direct loss of user ETH principal.** When user A's surplus ETH is stranded on the router, user B's WETH payment consumes it: the router wraps user A's ETH and sends it to the pool, then pulls only `value − strandedETH` WETH from user B. User A's ETH is permanently transferred to the pool on behalf of user B. User A receives no compensation and cannot recover the ETH. The loss is bounded only by the amount of ETH user A sent in excess of their swap requirement.

This affects both `MetricOmmSimpleRouter` (swap paths) and `MetricOmmPoolLiquidityAdder` (liquidity paths).

---

### Likelihood Explanation

**Medium.** The trigger requires two conditions:

1. A user sends excess `msg.value` to a payable router function without including `refundETH()` in the same multicall. This is a realistic user error — the protocol's own test suite demonstrates the correct pattern (multicall + `refundETH()`), implying users who call the function directly or build multicalls without the refund step will strand ETH.

2. A second caller (attacker or ordinary user) subsequently calls any WETH-input swap or liquidity add with `msg.value = 0` but with a WETH allowance. This is a normal, valid operation.

An attacker can monitor the router's ETH balance on-chain and deliberately trigger step 2 immediately after step 1 to extract the stranded ETH.

---

### Recommendation

Track the ETH contributed by the current call separately from any pre-existing router balance. One approach is to pass the original `msg.value` into `pay()` as a parameter and use only that amount as the native contribution, rather than reading `address(this).balance`. Alternatively, snapshot the balance at the start of each top-level entry point and pass the snapshot down to `pay()`, so only the current caller's ETH is eligible for wrapping.

---

### Proof of Concept

**Setup:** Router deployed with WETH. User A and User B both have ETH and WETH. User B has approved the router for WETH.

**Step 1 — User A strands ETH:**
```solidity
// User A calls exactInputSingle directly (no multicall, no refundETH)
// amountIn = 1_000 WETH, but sends 2_000 ETH
router.exactInputSingle{value: 2_000}(ExactInputSingleParams({
    pool: pool,
    tokenIn: address(weth),
    tokenOut: address(token1),
    zeroForOne: true,
    amountIn: 1_000,
    amountOutMinimum: 0,
    recipient: userA,
    deadline: block.timestamp,
    priceLimitX64: 0,
    extensionData: ""
}));
// pay() wraps 1_000 ETH, sends to pool. 1_000 ETH remains on router.
// Transaction ends. router.balance == 1_000.
```

**Step 2 — User B (attacker) exploits stranded ETH:**
```solidity
// User B calls exactInputSingle with msg.value = 0, amountIn = 2_000 WETH
// User B has WETH allowance of 2_000 to router
router.exactInputSingle{value: 0}(ExactInputSingleParams({
    pool: pool,
    tokenIn: address(weth),
    tokenOut: address(token1),
    zeroForOne: true,
    amountIn: 2_000,
    amountOutMinimum: 0,
    recipient: userB,
    deadline: block.timestamp,
    priceLimitX64: 0,
    extensionData: ""
}));
// pay(WETH, userB, pool, 2_000):
//   nativeBalance = 1_000  (user A's stranded ETH)
//   branch: nativeBalance > 0 && nativeBalance < value
//   → wraps 1_000 ETH (user A's), sends 1_000 WETH to pool
//   → pulls 1_000 WETH from user B, sends to pool
// Pool receives 2_000 WETH. User B pays only 1_000 WETH.
// User A loses 1_000 ETH permanently.
```

**Result:**
- `router.balance == 0` (user A's ETH is gone)
- User A lost 1,000 ETH with no recourse
- User B paid 1,000 WETH instead of 2,000 WETH for the same swap output

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L10-10)
```text
import {PeripheryPayments} from "./base/PeripheryPayments.sol";
```
