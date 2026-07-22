### Title
`pay()` consumes the router's entire `address(this).balance` for any WETH swap, allowing theft of any ETH stranded from a prior caller — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay()` helper in `PeripheryPayments` reads `address(this).balance` and uses **all of it** to subsidise any WETH-input swap callback. Because the router is payable and ETH can be left behind from a previous caller's `msg.value`, a subsequent attacker can call `exactInputSingle` (or any WETH-input swap) with `value = 0` and have the pool paid entirely from the victim's stranded ETH, receiving full swap output while paying nothing.

---

### Finding Description

`pay()` contains the following branch for WETH payments:

```solidity
uint256 nativeBalance = address(this).balance;   // entire router balance
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);  // pool paid from router ETH
} else if (nativeBalance > 0) {
    IWETH9(WETH).deposit{value: nativeBalance}();
    IERC20(WETH).safeTransfer(recipient, nativeBalance);
    IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
} else {
    IERC20(WETH).safeTransferFrom(payer, recipient, value);
}
``` [1](#0-0) 

The condition `nativeBalance > 0` is the direct analog of `address(this).balance != 0` in the HibernationDen report: it is trivially satisfiable by any prior caller who left ETH in the router.

**How ETH becomes stranded.** The `receive()` guard blocks plain ETH transfers:

```solidity
receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
}
``` [2](#0-1) 

However, `receive()` is **not** invoked when ETH arrives via `msg.value` on a payable function. Every swap entry-point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, `multicall`) is `payable`. A caller who sends `msg.value > amountIn` (e.g. to cover a WETH swap with native ETH) and omits the trailing `refundETH()` call leaves the surplus in the router permanently until the next transaction drains it.

**How the attacker drains it.** `exactInputSingle` stores `msg.sender` as the `payer` in transient storage:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
``` [3](#0-2) 

During the pool callback, `_justPayCallback` calls `pay(_getTokenToPay(), _getPayer(), msg.sender, amount)`:

```solidity
function _justPayCallback(int256 amount0Delta, int256 amount1Delta) private {
    pay(
      _getTokenToPay(),
      _getPayer(),
      msg.sender,
      uint256(MetricOmmSwapResults.extractPositiveAmount(amount0Delta, amount1Delta))
    );
}
``` [4](#0-3) 

If the attacker calls `exactInputSingle{value: 0}(tokenIn=WETH, amountIn=N)` and the router holds `N` wei of stranded ETH, `pay()` enters the `nativeBalance >= value` branch, wraps the victim's ETH, and transfers it to the pool — the attacker's own WETH balance is never touched. The attacker receives full swap output at zero cost.

---

### Impact Explanation

**Direct loss of user principal.** Any ETH left in the router by a prior caller is immediately claimable by the next WETH-input swap. The attacker receives the full swap output (tokenOut) while paying nothing; the victim's ETH is consumed by the pool. Loss equals the stranded ETH amount, which can be arbitrarily large (e.g. a user who sent 1 ETH as `msg.value` for a 0.5 ETH swap and forgot `refundETH()`).

---

### Likelihood Explanation

**Medium.** The intended usage pattern documented by the protocol is a `multicall` that ends with `refundETH()`. Users who call `exactInputSingle` or `exactInput` directly with `msg.value > amountIn` — a natural mistake when paying with native ETH — leave ETH stranded. An attacker monitoring the router's balance can front-run the victim's `refundETH()` transaction or simply be the next caller. No privileged access, no non-standard token, and no malicious pool setup is required.

---

### Recommendation

Replace the global `address(this).balance` read with the ETH that was explicitly sent in the **current** call. One approach: pass `msg.value` (or a tracked per-call ETH budget) into `pay()` and cap native consumption to that amount. Alternatively, require callers to pre-wrap ETH themselves and remove the native-ETH subsidy path entirely, relying on `unwrapWETH9` / `refundETH` for the reverse direction only.

---

### Proof of Concept

```
// Step 1 – victim sends excess ETH
router.exactInputSingle{value: 1 ether}(ExactInputSingleParams({
    tokenIn: address(weth),
    amountIn: 0.5 ether,   // only 0.5 ETH needed
    ...
}));
// 0.5 ETH remains in router; victim does NOT call refundETH()

// Step 2 – attacker (in a later tx, or front-running victim's refundETH)
router.exactInputSingle{value: 0}(ExactInputSingleParams({
    tokenIn: address(weth),
    amountIn: 0.5 ether,   // matches stranded balance
    ...
}));
// pay() sees nativeBalance = 0.5 ETH >= value = 0.5 ETH
// wraps victim's 0.5 ETH → sends WETH to pool
// attacker's own WETH balance: unchanged
// attacker receives full tokenOut; victim loses 0.5 ETH
```

The root cause is `nativeBalance > 0` / `nativeBalance >= value` in `pay()` reading `address(this).balance` without bounding it to the ETH the **current caller** supplied. [5](#0-4)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
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
