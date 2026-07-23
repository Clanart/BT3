The vulnerability is real. Let me trace the exact code path to confirm.

**Key code path:**

`exactInputSingle` sets `payer = msg.sender` in transient storage: [1](#0-0) 

`_justPayCallback` calls `pay` with that stored payer: [2](#0-1) 

The `pay` function, when `token == WETH`, checks `address(this).balance` — the router's **total** native ETH balance, not the current caller's `msg.value`: [3](#0-2) 

If `address(this).balance >= value`, it wraps and transfers that ETH without pulling anything from `payer`. There is no attribution — it consumes whatever ETH is sitting on the router, regardless of who deposited it.

---

### Title
Cross-Transaction ETH Theft via Unattributed Native Balance in `PeripheryPayments.pay` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`PeripheryPayments.pay` uses `address(this).balance` to fund WETH payments. Any ETH stranded on the router from a prior user's unrefunded payable call is silently consumed to fund a subsequent user's WETH swap, with no pull from the subsequent user's own allowance.

### Finding Description
When `token == WETH` and `payer != address(this)`, `pay` reads the router's total native ETH balance:

```solidity
uint256 nativeBalance = address(this).balance;
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);
}
``` [4](#0-3) 

This balance is global — it includes ETH left over from any prior transaction where a user sent more ETH than their `amountIn` and did not call `refundETH`. The function has no mechanism to distinguish ETH belonging to the current caller from ETH stranded by a previous caller.

The `receive()` guard only blocks direct ETH transfers from non-WETH addresses: [5](#0-4) 

ETH sent via `exactInputSingle{value: ...}` or `multicall{value: ...}` is accepted freely and persists on the router across transactions if `refundETH` is not called.

### Impact Explanation
**Direct loss of user principal.** User A's stranded ETH is consumed to fund User B's swap. User A permanently loses the unrefunded ETH (it is wrapped into WETH and transferred to the pool on User B's behalf). User B receives a fully subsidized swap — their WETH allowance is not touched at all.

### Likelihood Explanation
The ETH-input pattern is explicitly documented and tested as a multicall flow where users send `{value: amountIn}` and optionally append `refundETH`. Any user who calls `exactInputSingle{value: X}` with `amountIn < X` directly (not via multicall, or via multicall without `refundETH`) leaves residue. This is a realistic mistake. An attacker can monitor the mempool or router balance and immediately follow with a zero-value WETH swap to drain the residue.

### Recommendation
Track the ETH available for the **current** call by passing `msg.value` (or a per-call budget) into `pay` and capping native ETH usage to that amount. Alternatively, only allow native ETH consumption equal to the ETH sent in the current top-level call, storing `msg.value` at entry and decrementing it as it is used, so stranded ETH from prior transactions is never eligible to fund a new caller's payment.

### Proof of Concept

```
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
//   nativeBalance = 0.5 ether >= 0.5 ether → wraps User A's ETH, no pull from userB
// Result: User A loses 0.5 ether; User B's swap is fully funded for free
```

The invariant `each user's WETH payment is funded exclusively from their own ETH or WETH allowance` is broken. User A's stranded ETH is consumed without consent. [6](#0-5)

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
