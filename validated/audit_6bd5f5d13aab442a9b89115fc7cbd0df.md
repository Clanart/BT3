Audit Report

## Title
Cross-Transaction ETH Theft via Unattributed Native Balance in `pay` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.pay` funds WETH payments using `address(this).balance`, which is the router's global native ETH balance across all transactions. ETH stranded on the router from a prior user's unrefunded payable call is silently consumed to fund a subsequent user's WETH swap, with no pull from the subsequent user's own WETH allowance. The prior user permanently loses their ETH.

## Finding Description
`exactInputSingle` is `payable` and stores `msg.sender` as payer in transient storage. [1](#0-0) 

When the pool calls back, `_justPayCallback` invokes `pay` with that stored payer and the owed amount. [2](#0-1) 

Inside `pay`, when `token == WETH` and `payer != address(this)`, the function reads the router's **total** native balance — not the current call's `msg.value`:

```solidity
uint256 nativeBalance = address(this).balance;
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);
}
``` [3](#0-2) 

This balance is global. If User A calls `exactInputSingle{value: 1 ether}` with `amountIn: 0.5 ether`, the `pay` call wraps only 0.5 ETH; the remaining 0.5 ETH persists on the router across transaction boundaries. The `receive()` guard only blocks direct ETH transfers from non-WETH addresses — it does not prevent ETH from accumulating via payable function calls. [4](#0-3) 

User B then calls `exactInputSingle{value: 0}` with `tokenIn: WETH, amountIn: 0.5 ether`. The `pay` function sees `nativeBalance = 0.5 ether >= 0.5 ether`, wraps User A's stranded ETH, and transfers WETH to the pool — without touching User B's WETH allowance. There is no mechanism to attribute which ETH belongs to which caller.

## Impact Explanation
Direct loss of user principal. User A's stranded ETH is consumed without consent to fund User B's swap. User A permanently loses the unrefunded ETH (it is wrapped into WETH and transferred to the pool on User B's behalf). User B receives a fully subsidized swap — their WETH allowance is never touched. This is a Critical/High direct loss of funds by an unprivileged caller.

## Likelihood Explanation
The ETH-input pattern is a standard multicall flow where users send `{value: amountIn}` and optionally append `refundETH`. Any user who calls `exactInputSingle{value: X}` with `amountIn < X` directly (not via multicall, or via multicall without `refundETH`) leaves residue. An attacker can monitor the mempool or the router's on-chain balance and immediately follow with a zero-value WETH swap to drain the residue. No special privileges are required.

## Recommendation
Track the ETH available for the current call by storing `msg.value` at the top-level entry point and passing it (or a per-call budget) into `pay`, capping native ETH usage to that amount. Alternatively, store `msg.value` in transient storage at entry and decrement it as it is consumed, so ETH stranded from prior transactions is never eligible to fund a new caller's payment.

## Proof of Concept
```solidity
// Tx 1 — User A
router.exactInputSingle{value: 1 ether}(ExactInputSingleParams({
    tokenIn: WETH, amountIn: 0.5 ether, ...
}));
// User A forgets refundETH → 0.5 ether stranded on router

// Tx 2 — Attacker (User B), value: 0, no WETH approval needed
router.exactInputSingle{value: 0}(ExactInputSingleParams({
    tokenIn: WETH, amountIn: 0.5 ether, ...
}));
// pay(WETH, userB, pool, 0.5 ether):
//   nativeBalance = address(this).balance = 0.5 ether >= 0.5 ether
//   → wraps User A's ETH, no safeTransferFrom on userB
// Result: User A loses 0.5 ether; User B's swap is fully funded for free
``` [5](#0-4)

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
