Audit Report

## Title
Stranded ETH from prior user consumed to fund subsequent WETH swap, causing direct ETH principal loss — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.pay` reads the raw `address(this).balance` when settling a WETH payment, with no per-call or per-user ETH accounting. ETH left on the router by a prior user — via an overpaying payable call that omits `refundETH`, or via a payable call whose `tokenIn` is not WETH — is silently consumed to satisfy a subsequent user's WETH payment obligation, permanently destroying the prior user's principal.

## Finding Description
`multicall` and `exactInputSingle` are both `payable`, so any caller can deposit ETH onto the router as `msg.value`. [1](#0-0) [2](#0-1) 

`refundETH` is opt-in; any ETH not explicitly reclaimed persists on the router across transactions. [3](#0-2) 

The `receive()` guard only blocks *direct* ETH pushes from non-WETH addresses; it does not prevent ETH accumulation via `msg.value` in payable entrypoints. [4](#0-3) 

When a WETH swap fires its callback, `_justPayCallback` calls `pay(WETH, payer, pool, value)` where `payer` is the current user (`msg.sender` captured at swap entry). [5](#0-4) [6](#0-5) 

Inside `pay`, the WETH branch unconditionally reads `address(this).balance` — the entire contract balance, not just ETH the current caller sent — and wraps it first before falling back to `safeTransferFrom`: [7](#0-6) 

If `nativeBalance >= value`, the contract wraps exactly `value` of its ETH and transfers WETH to the pool; the current user's allowance is never touched. If `0 < nativeBalance < value`, only the remainder is pulled from the user. In both cases, any ETH belonging to a prior user is consumed without their consent.

## Impact Explanation
- **User A** permanently loses their ETH: it is wrapped and transferred to the pool to settle User B's swap with no recovery path.
- **User B** receives a fully or partially subsidized swap — their WETH allowance pull is reduced or eliminated entirely.
- This is direct loss of user principal meeting High severity thresholds under the allowed impact gate (direct loss of user principal, swap conservation failure: pool receives input that was not owed by the swapping user).

## Likelihood Explanation
The precondition — ETH stranded on the router — arises from ordinary, non-malicious usage:
1. A user calls `multicall{value: N}([exactInputSingle(tokenIn=WETH, amountIn=M, ...)])` where `N > M` (overpayment for slippage headroom) and omits `refundETH`; `N - M` ETH remains.
2. A user calls any payable entrypoint with `msg.value > 0` while `tokenIn` is not WETH; the ETH is never consumed by `pay` and stays on the router.

No attacker coordination is required. The next WETH-paying swap on the same router automatically exploits the stranded balance. The condition is repeatable and requires no privileged access.

## Recommendation
Introduce per-call ETH attribution using transient storage. At the top of each payable entrypoint, record `msg.value` in a transient slot (EIP-1153). Inside `pay`, read and deduct from that tracked budget rather than from raw `address(this).balance`. This ensures only ETH the *current* caller explicitly sent in the *current* call is eligible for wrapping. Alternatively, require that WETH payments always use `safeTransferFrom` and expose a separate `exactInputSingleNative` entrypoint that explicitly wraps `msg.value` before the swap, removing the hybrid branch from `pay` entirely.

## Proof of Concept
```
// Precondition: router has 0 ETH balance.

// Step 1 — strand ETH (overpayment scenario):
// User A calls multicall{value: 2 ether}([
//   exactInputSingle(tokenIn=WETH, amountIn=1e18, ...)
// ])
// pay(WETH, userA, pool, 1e18): nativeBalance=2e18 >= value=1e18
//   → wraps 1 ETH, sends WETH to pool (User A's swap settles correctly)
//   → 1 ETH remains on router (no refundETH included)

// Step 2 — exploit:
// User B calls exactInputSingle{value: 0}(tokenIn=WETH, amountIn=1e18, ...)
// Callback fires → pay(WETH, userB, pool, 1e18)
//   nativeBalance = 1e18 (User A's stranded ETH)
//   nativeBalance >= value → wraps User A's 1 ETH, safeTransfer WETH to pool
//   User B's WETH allowance: 0 pulled

// Assert:
//   User A: lost 1 ETH with no recovery
//   User B: swap settled at zero WETH cost
//   Router ETH balance: 0
```

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
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
