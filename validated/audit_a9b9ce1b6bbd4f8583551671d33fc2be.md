The code confirms the vulnerability. All cited references match the actual production code exactly:

- `pay()` at [1](#0-0)  uses `address(this).balance` globally with no per-caller attribution.
- `refundETH()` at [2](#0-1)  sends to `msg.sender`, not the original depositor.
- `_justPayCallback` at [3](#0-2)  passes the current caller's payer to `pay()`.
- `multicall` at [4](#0-3)  is `payable` and leaves residual ETH in the contract when `refundETH` is omitted.
- The `receive()` guard at [5](#0-4)  only blocks plain ETH transfers, not `msg.value` sent to `payable` functions.

---

Audit Report

## Title
Stranded ETH in Router Consumed by Subsequent WETH Swap — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`PeripheryPayments.pay()` uses `address(this).balance` as an unattributed shared pool when paying WETH. Any ETH left in the router from a prior user's incomplete multicall (missing `refundETH`) can be silently consumed to fund a subsequent user's WETH swap, with zero pull from the attacker's wallet. The prior user loses their stranded ETH with no recourse.

## Finding Description

In `pay()`, when `token == WETH` and `payer != address(this)`, the function reads the entire contract ETH balance at L74 (`uint256 nativeBalance = address(this).balance`) and, if `nativeBalance >= value`, wraps and forwards that ETH to the pool without pulling anything from `payer` (L75-77). There is no accounting of which caller deposited the ETH currently in `address(this).balance`.

ETH accumulates in the router when users call `multicall{value: X}(...)` and omit `refundETH`. The `receive()` guard (L32-34) only blocks plain ETH transfers; `msg.value` sent to `payable` entry points (`multicall`, `exactInputSingle`, etc.) bypasses it entirely and stays in the contract.

`exactInputSingle` sets `msg.sender` as payer via `_setNextCallbackContext` (L71), and `_justPayCallback` (L192-199) passes that payer to `pay()`. When User B calls `exactInputSingle{value: 0}` with `tokenIn = WETH`, `pay()` finds User A's stranded ETH, wraps it, and delivers it to the pool — User B's wallet is never touched.

`refundETH` (L58-63) sends to `msg.sender`, not the original depositor, so a third party calling it would redirect the ETH rather than return it to User A.

## Impact Explanation

Direct loss of user principal. User A's ETH is irreversibly consumed to settle User B's swap obligation. User A receives nothing; User B receives full swap output while paying zero WETH from their wallet. Loss equals the stranded ETH amount, up to the full `msg.value` of User A's multicall. This is a Critical/High direct loss of user funds meeting Sherlock thresholds.

## Likelihood Explanation

The precondition (ETH stranded in the router) is realistic and common: users routinely omit `refundETH` when they expect exact consumption, and slippage or partial fills leave residual ETH. An attacker can monitor the mempool for multicalls omitting `refundETH` and immediately follow with a WETH swap sized to exactly the stranded amount. The attack requires no special privileges, no malicious token behavior, and is repeatable on every such occurrence.

## Recommendation

Track per-call deposited ETH using transient storage. At the start of each top-level entry point, record `msg.value` as the authorized native balance for this call. In `pay()`, deduct only from that authorized amount rather than from `address(this).balance` globally, and revert if the authorized balance is insufficient. Clear the authorized balance at call end. Alternatively, enforce that `address(this).balance` is zero at the start of every non-multicall entry point, and that multicall tracks and enforces per-call ETH budgets.

## Proof of Concept

```solidity
// Step 1: User A sends 1 ETH, swaps 0.9 ETH worth of WETH, omits refundETH
router.multicall{value: 1 ether}([
    abi.encodeCall(router.exactInputSingle, (ExactInputSingleParams({
        tokenIn: WETH, tokenOut: token1, amountIn: 0.9 ether, ...
    })))
    // no refundETH — 0.1 ETH stranded in router
]);
// address(router).balance == 0.1 ETH

// Step 2: Attacker (User B) calls exactInputSingle for WETH, sends 0 ETH
router.exactInputSingle{value: 0}(ExactInputSingleParams({
    tokenIn: WETH, tokenOut: token1, amountIn: 0.1 ether, ...
}));
// Inside pay(WETH, UserB, pool, 0.1 ether):
//   nativeBalance = 0.1 ETH >= value = 0.1 ETH  → L75 branch taken
//   WETH.deposit{value: 0.1 ETH}(); WETH.safeTransfer(pool, 0.1 WETH);
//   UserB pulls 0 WETH from wallet

// Result: User A lost 0.1 ETH; User B received full swap output for free
assert(address(router).balance == 0);
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
