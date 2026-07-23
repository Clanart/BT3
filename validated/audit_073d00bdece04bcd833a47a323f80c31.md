### Title
Stranded native ETH on the router is unconditionally consumed by any subsequent WETH-input swap, allowing theft of prior users' funds — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` uses `address(this).balance` — the router's **total** native ETH balance — to subsidize WETH payments. Any ETH left on the router from a previous user's transaction (a common occurrence when `refundETH()` is not called) is silently consumed by the next caller who performs a WETH-input swap, with no payment pulled from that caller. The prior user loses their ETH; the later caller receives output tokens for free.

---

### Finding Description

`PeripheryPayments.pay()` handles WETH payments as follows:

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
``` [1](#0-0) 

When `nativeBalance >= value`, the function deposits the router's own ETH as WETH and transfers it to the pool. It does **not** call `safeTransferFrom(payer, ...)`. The `payer` (the current swap caller) pays nothing.

The router accumulates native ETH via `msg.value` on any `payable` entry point (`multicall`, `exactInputSingle`, `exactOutputSingle`, etc.). The intended pattern is for users to append a `refundETH()` call in a multicall to recover unused ETH. If they omit it, the ETH remains on the router across transactions. [2](#0-1) 

The `receive()` guard only blocks non-WETH ETH pushes; it does not prevent ETH from accumulating via `msg.value`: [3](#0-2) 

The `pay()` function is invoked from `_justPayCallback`, which is called from `metricOmmSwapCallback` during every WETH-input swap: [4](#0-3) 

The `payer` stored in transient storage is the swap initiator (attacker), but `pay()` bypasses the `safeTransferFrom(payer, ...)` path entirely when `nativeBalance >= value`. [5](#0-4) 

---

### Impact Explanation

**Direct loss of user principal.** User A's stranded ETH is transferred to a pool on behalf of an attacker. The attacker receives output tokens worth that ETH without spending anything. User A's ETH is permanently lost. The loss is 100% of the stranded amount, which can be arbitrarily large (e.g., a user who sends 1 ETH for a WETH swap and forgets `refundETH()` loses the entire unused portion).

---

### Likelihood Explanation

The "send ETH + swap + refundETH in multicall" pattern is the documented usage for native ETH swaps. Omitting `refundETH()` is a common user error (the test suite explicitly tests the refund path, implying it is easy to forget). Once ETH is stranded, any attacker can observe the router's ETH balance on-chain and immediately drain it with a single `exactInputSingle` call using WETH as `tokenIn`. No privileged access, no special setup, and no malicious pool is required.

---

### Recommendation

Track the ETH contributed by `msg.value` in the **current transaction** using a transient slot (e.g., store `msg.value` at entry to `multicall`/`exactInputSingle`/`exactOutputSingle` and decrement it as it is consumed). In `pay()`, only use native ETH up to the amount attributable to the current caller's `msg.value`, not `address(this).balance`. Alternatively, require that `address(this).balance` is zero at the start of every top-level entry point (revert if residue is detected), forcing users to always clear the router before use.

---

### Proof of Concept

```
1. User A calls:
   router.multicall{value: 1 ether}([
     exactInputSingle(pool, tokenIn=WETH, amountIn=0.5 ether, ...)
     // refundETH() omitted by mistake
   ])
   → 0.5 ETH is used for the swap; 0.5 ETH remains on the router.

2. Attacker observes router.balance == 0.5 ETH on-chain.

3. Attacker calls (with msg.value = 0):
   router.exactInputSingle(pool, tokenIn=WETH, amountIn=0.5 ether, ...)

4. Pool executes swap, sends output tokens to attacker, calls metricOmmSwapCallback.

5. _justPayCallback → pay(WETH, attacker, pool, 0.5 ether):
   nativeBalance = 0.5 ether >= value = 0.5 ether
   → WETH.deposit{value: 0.5 ether}()   // uses User A's ETH
   → WETH.transfer(pool, 0.5 ether)     // pool is paid
   // safeTransferFrom(attacker, ...) is NEVER called

6. Attacker receives output tokens worth 0.5 ETH. User A's 0.5 ETH is gone.
```

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

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L29-32)
```text
  function _setNextCallbackContext(address pool, uint8 callbackMode, address payer, address tokenToPay) internal {
    _requireFactoryPool(pool);
    TransientCallbackPool.set(pool, callbackMode, payer, tokenToPay);
  }
```
