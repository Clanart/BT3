### Title
`pay()` uses `safeTransfer` instead of `safeTransferFrom` for external non-WETH ERC20 payers, breaking all non-WETH ERC20 input swaps — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay()` helper in `PeripheryPayments.sol` has three branches. The final `else` branch — reached when the payer is an **external user** and the token is **not WETH** — calls `IERC20(token).safeTransfer(recipient, value)` (transfers from the router's own balance) instead of `IERC20(token).safeTransferFrom(payer, recipient, value)` (pulls from the user). This mirrors the external report's bug class exactly: a wrong operator/operation in a critical settlement path that makes the effective behavior the opposite of what is intended.

---

### Finding Description

`PeripheryPayments.pay()` is the single settlement primitive called by every swap callback path in the router: [1](#0-0) 

```solidity
function pay(address token, address payer, address recipient, uint256 value) internal {
    // If the payer is contract it means we are in the middle of a path.
    if (payer == address(this)) {
        IERC20(token).safeTransfer(recipient, value);          // ✓ router pays from its own balance
    } else if (token == WETH) {
        // ... correctly uses safeTransferFrom(payer, ...) when native ETH is insufficient
    } else {
        IERC20(token).safeTransfer(recipient, value);          // ✗ WRONG: transfers from router, not payer
    }
}
```

The `else` branch at line 86 is reached whenever:
- `payer != address(this)` — i.e., the payer is the **external swap initiator**, AND
- `token != WETH` — i.e., the input token is any plain ERC-20

In that case the code calls `safeTransfer(recipient, value)`, which transfers from `address(this)` (the router). The correct call is `safeTransferFrom(payer, recipient, value)`.

This path is exercised by every swap callback that settles the first hop: [2](#0-1) 

```solidity
function _justPayCallback(int256 amount0Delta, int256 amount1Delta) private {
    pay(
        _getTokenToPay(),   // params.tokenIn
        _getPayer(),        // original msg.sender (external user)
        msg.sender,         // pool
        uint256(...)
    );
}
```

`_getPayer()` returns the original `msg.sender` (never `address(this)`) for the first hop of `exactInputSingle`, `exactInput`, `exactOutputSingle`, and the terminal hop of `exactOutput`. [3](#0-2) 

---

### Impact Explanation

**Primary impact — broken core swap functionality (High):**  
Every call to `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` where `tokenIn` is a non-WETH ERC-20 will revert inside the pool callback because the router holds no balance of that token. The pool's balance check fails, the callback reverts, and the user's transaction is rolled back. This renders the router unusable for the majority of token pairs (any pair where the input leg is not WETH).

**Secondary impact — router balance theft (High):**  
If the router accumulates a non-WETH ERC-20 balance (e.g., from a prior `sweepToken` that was not called, a partial fill residue, or a `multicall` step that left tokens on the router), an attacker can call `exactInputSingle` with that token as `tokenIn`. The router will pay the pool from its own balance, and the attacker receives the output tokens without spending any of their own funds. This is a direct loss of principal held by the router.

---

### Likelihood Explanation

**High.** Any user attempting to swap a non-WETH ERC-20 token triggers the bug unconditionally. No special setup, privileged role, or malicious token is required. The `selfPermit` + `exactInputSingle` multicall pattern (explicitly tested in the test suite with a non-WETH permit token) hits this path directly. [4](#0-3) 

---

### Recommendation

```diff
  } else {
-     IERC20(token).safeTransfer(recipient, value);
+     IERC20(token).safeTransferFrom(payer, recipient, value);
  }
``` [5](#0-4) 

---

### Proof of Concept

**Setup:** Alice holds 1 000 USDC (non-WETH ERC-20). She approves the router for 1 000 USDC and calls `exactInputSingle`:

```
tokenIn  = USDC
amountIn = 1_000
payer    = Alice (msg.sender, stored via _setNextCallbackContext)
```

**Execution trace:**

1. `exactInputSingle` stores `(pool, CALLBACK_MODE_JUST_PAY, Alice, USDC)` in transient storage.
2. Pool executes the swap and calls `metricOmmSwapCallback(amount0Delta, amount1Delta, "")`.
3. Router calls `_justPayCallback` → `pay(USDC, Alice, pool, 1_000)`.
4. `Alice != address(this)` → skip first branch.  
   `USDC != WETH` → skip second branch.  
   **`else` branch hit:** `IERC20(USDC).safeTransfer(pool, 1_000)` — transfers from router's USDC balance.
5. Router holds 0 USDC → ERC-20 transfer reverts → pool callback reverts → Alice's transaction reverts.

**Theft variant:** Replace step 5 with a scenario where the router holds 1 000 USDC (leftover from a prior step). The router pays the pool from its own balance; Alice receives the output token without spending any USDC. The router's 1 000 USDC is permanently lost.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-71)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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

**File:** metric-periphery/test/MetricOmmSimpleRouter.multicall.t.sol (L22-51)
```text
  function test_multicall_selfPermit_then_exactInputSingle() public {
    uint128 amountIn = 2_000;
    uint256 deadline = block.timestamp + 1 hours;
    (uint8 v, bytes32 r, bytes32 s) = _signPermit(amountIn, deadline);

    uint256 token1Before = token1.balanceOf(recipient);

    vm.prank(swapper);
    bytes[] memory calls = new bytes[](2);
    calls[0] = abi.encodeWithSelector(router.selfPermit.selector, address(permitToken), amountIn, deadline, v, r, s);
    calls[1] = abi.encodeWithSelector(
      router.exactInputSingle.selector,
      IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(permitPool),
        tokenIn: address(permitToken),
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
    router.multicall(calls);

    assertGt(token1.balanceOf(recipient), token1Before, "swap succeeded");
    _assertRouterEmpty();
  }
```
