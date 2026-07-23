The code confirms the vulnerability. All execution steps trace correctly through the production code:

- `exactInputSingle` stores `payer = msg.sender` via `_setNextCallbackContext` [1](#0-0) 
- `_justPayCallback` calls `pay(_getTokenToPay(), _getPayer(), msg.sender, amount)` [2](#0-1) 
- `pay()` branches on `address(this).balance` with no check that the ETH belongs to the current caller [3](#0-2) 
- `multicall` is `payable`, allowing ETH to accumulate; `receive()` only blocks non-WETH direct pushes [4](#0-3) 
- `refundETH()` is not enforced and sends to `msg.sender` only when called [5](#0-4) 

---

Audit Report

## Title
Router ETH Balance Consumed Instead of Payer's WETH When Router Holds Residual ETH — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.pay()` unconditionally uses the router's native ETH balance when `token == WETH` and `address(this).balance >= value`, without verifying that the ETH was deposited by the current caller. Any ETH stranded in the router from a prior user's multicall that omitted `refundETH()` can be consumed by a subsequent attacker who calls a WETH-input swap with zero ETH sent and zero WETH approved, receiving full swap output at no cost.

## Finding Description
In `pay()` (`PeripheryPayments.sol` L73–84), when `token == WETH` and `payer != address(this)`, the function checks `address(this).balance` first:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value); // payer's WETH never touched
    } else if (nativeBalance > 0) { ... }
    else { IERC20(WETH).safeTransferFrom(payer, recipient, value); }
}
```

The `payer` stored in transient storage is completely bypassed when `nativeBalance >= value`. ETH accumulates in the router when a user calls `multicall{value: X}([exactInputSingle(amountIn=Y)])` with `X > Y` and omits `refundETH()`. The `receive()` guard (`msg.sender != WETH`) only blocks direct ETH pushes and does not prevent accumulation via `multicall{value}`. Once ETH is stranded, any caller can trigger `exactInputSingle(tokenIn=WETH, amountIn=stranded_amount)` with no ETH and no WETH approval; `pay()` wraps the router's ETH and pays the pool, and the attacker receives the output tokens.

## Impact Explanation
Direct loss of user principal. The victim's stranded ETH is fully transferred to the attacker as swap output. This matches the "Critical/High direct loss of user principal" allowed impact. The exact corrupted value is `address(router).balance` (the victim's residual ETH), which is consumed without authorization.

## Likelihood Explanation
The native ETH swap pattern requires users to manually append `refundETH()` to their multicall — the protocol's own test suite (`MetricOmmSimpleRouter.native.t.sol` L106–133) demonstrates this exact pattern. Omitting `refundETH()` is a realistic user error. Once ETH is stranded, exploitation requires only a single public router call with no special permissions, no front-running window dependency, and is repeatable by any address.

## Recommendation
Track per-transaction ETH contributions in transient storage (e.g., store `msg.value` at `multicall`/swap entry) and restrict `pay()` to consume at most that amount from the router's ETH balance. Alternatively, require that the ETH-from-router path is only reachable when the originating top-level call had `msg.value > 0`, or always use `safeTransferFrom(payer, ...)` when the router's ETH was not deposited by the current top-level caller.

## Proof of Concept
```solidity
function test_strandedEthStolenViaWethSwap() public {
    // Victim sends 1 ETH, swaps 0.5 ETH, omits refundETH()
    vm.deal(victim, 1 ether);
    vm.prank(victim);
    router.multicall{value: 1 ether}(
        _encodeExactInputSingle(address(weth), address(token1), 0.5 ether)
    );
    assertEq(address(router).balance, 0.5 ether); // stranded ETH

    // Attacker: no ETH, no WETH approval
    vm.prank(attacker);
    uint256 out = router.exactInputSingle(
        ExactInputSingleParams({tokenIn: address(weth), amountIn: 0.5 ether, ...})
    );
    assertGt(out, 0);                              // attacker received tokens
    assertEq(address(router).balance, 0);          // victim's ETH drained
}
```

### Citations

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
