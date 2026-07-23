Audit Report

## Title
Router `pay()` Consumes Unattributed Native ETH Balance, Enabling Cross-Transaction Residue Theft of Stranded WETH-Input ETH — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
The `pay()` function in `PeripheryPayments.sol` reads `address(this).balance` — the router's entire native ETH balance — when settling WETH payment obligations, with no per-caller attribution. Any ETH stranded on the router from a prior transaction (due to `msg.value > amountIn` without an atomic `refundETH`) is indistinguishable from the current caller's ETH and will be consumed first. An attacker can submit a WETH-input swap with `msg.value = 0` and have their obligation settled entirely from the victim's stranded ETH.

## Finding Description
`pay()` at [1](#0-0)  reads `address(this).balance` without any scoping to the current caller's `msg.value`. All entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, `multicall`) are `payable`, so any `msg.value` surplus lands directly in `address(this).balance` and persists across transaction boundaries.

The `receive()` guard at [2](#0-1)  only blocks direct ETH pushes from non-WETH addresses; it does not prevent `msg.value` attached to `payable` function calls from accumulating on the router.

The exploit path:
1. Victim calls `exactInputSingle{value: 2000}(amountIn=1000, tokenIn=WETH)`. The callback fires `_justPayCallback` → `pay(WETH, victim, pool, 1000)`. At [3](#0-2) , `nativeBalance (2000) >= value (1000)`, so 1000 ETH is deposited and transferred; 1000 ETH remains stranded.
2. Attacker calls `exactInputSingle{value: 0}(amountIn=1000, tokenIn=WETH)`. At [4](#0-3) , callback context is set with `msg.sender` (attacker) as payer. The pool calls back, `_justPayCallback` at [5](#0-4)  calls `pay(WETH, attacker, pool, 1000)`. `nativeBalance (1000) >= value (1000)` — the victim's stranded ETH funds the attacker's swap entirely.
3. Victim calls `refundETH()` at [6](#0-5)  and receives 0 ETH back.

## Impact Explanation
Direct loss of user principal. The victim loses up to `msg.value − amountIn` ETH per transaction. The attacker receives a full WETH→TOKEN swap at zero cost. This satisfies the "Critical/High direct loss of user principal" gate.

## Likelihood Explanation
Medium. The precondition — ETH stranded on the router — arises whenever a user sends `msg.value > amountIn` to a WETH-input swap without atomically including `refundETH` in the same `multicall`, or submits `refundETH` as a separate follow-up transaction. Both patterns are common (users over-provision ETH to avoid reverts). An attacker only needs to monitor `address(router).balance` in the mempool and front-run the victim's `refundETH`.

## Recommendation
1. **Atomic refund enforcement**: Require that every WETH-input swap with `msg.value` includes `refundETH` as the final step of the same `multicall`. Enforce via NatSpec and integration guides.
2. **Scoped native balance tracking**: Track the current call's `msg.value` contribution separately (e.g., pass it as a parameter or store it in transient storage at entry) and limit `pay()`'s native ETH consumption to that scoped amount, reverting or pulling the remainder from the payer's WETH allowance.
3. **Guard in `pay()`**: When `payer != address(this)` and `token == WETH`, cap native ETH consumed to `min(nativeBalance, value)` and always pull the remainder from `payer` via `safeTransferFrom`, never silently consuming unattributed router balance.

## Proof of Concept
```
Block N:
  Victim calls exactInputSingle{value: 2000}(tokenIn=WETH, amountIn=1000, ...)
  → pay(WETH, victim, pool, 1000): nativeBalance=2000 >= 1000
    → deposits 1000 ETH as WETH, transfers to pool ✓
    → 1000 ETH stranded on router

Block N (same or next):
  Attacker calls exactInputSingle{value: 0}(tokenIn=WETH, amountIn=1000, ...)
  → _setNextCallbackContext(pool, JUST_PAY, attacker, WETH)
  → pool.swap() → metricOmmSwapCallback → _justPayCallback
  → pay(WETH, attacker, pool, 1000): nativeBalance=1000 >= 1000
    → deposits 1000 ETH (victim's) as WETH, transfers to pool
    → attacker receives TOKEN output, spends 0 ETH/WETH ✓

  Victim calls refundETH():
  → address(router).balance = 0 → victim receives 0 ETH
  → victim net loss: 1000 ETH
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L74-77)
```text
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
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
