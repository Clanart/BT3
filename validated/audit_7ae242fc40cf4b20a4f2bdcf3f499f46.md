### Title
ETH Sent With Non-WETH Swaps Is Silently Consumed by Subsequent WETH Swaps, Causing Permanent Loss of User Funds — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay()` function in `PeripheryPayments.sol` uses `address(this).balance` — the router's **total** ETH balance — when deciding how much native ETH to wrap for a WETH payment. Because every swap entry-point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, `addLiquidityExactShares`, etc.) is `payable` and accepts ETH unconditionally, a user who accidentally sends ETH while specifying a non-WETH `tokenIn` leaves that ETH stranded in the router. Any subsequent caller whose `tokenIn` **is** WETH will have their payment silently covered by the stranded ETH, permanently draining the original sender's funds.

---

### Finding Description

**Root cause — `PeripheryPayments.pay()` uses the whole contract balance:** [1](#0-0) 

```solidity
function pay(address token, address payer, address recipient, uint256 value) internal {
    if (payer == address(this)) {
        IERC20(token).safeTransfer(recipient, value);
    } else if (token == WETH) {
        uint256 nativeBalance = address(this).balance;   // ← total balance, not msg.value
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

**How ETH gets stranded — every swap entry-point is `payable` with no guard:** [2](#0-1) 

`exactInputSingle` (and all other swap functions) is `payable` and contains no check that `msg.value == 0` when `tokenIn != WETH`. When a user sends ETH with a non-WETH swap, the `pay()` callback takes the `else` branch (`safeTransferFrom`) and the ETH is never touched — it accumulates in the router.

**The `receive()` guard does not help:** [3](#0-2) 

`receive()` only blocks plain ETH transfers (no calldata). ETH sent alongside a function call is accepted by the `payable` modifier before `receive()` is ever consulted, so the guard is irrelevant here.

**Subsequent WETH swap silently consumes the stranded ETH:**

When the next caller executes a WETH swap, `_setNextCallbackContext` records `payer = msg.sender` and `tokenToPay = WETH`. [4](#0-3) 

Inside `_justPayCallback`, `pay(WETH, userB, pool, value)` is called. Because `address(this).balance` now contains the stranded ETH from User A, the `nativeBalance >= value` branch fires, wraps User A's ETH, and transfers WETH to the pool — **without pulling a single token from User B**. [5](#0-4) 

The same vulnerability exists in `MetricOmmPoolLiquidityAdder`, which inherits `PeripheryPayments` and exposes `payable` liquidity functions. [6](#0-5) 

---

### Impact Explanation

- **User A** calls `exactInputSingle` with `tokenIn = USDC` and accidentally sends 1 ETH. The swap succeeds (USDC is pulled via `safeTransferFrom`), but 1 ETH is stranded in the router.
- **User B** calls `exactInputSingle` with `tokenIn = WETH`, `amountIn = 1 ETH`, sending 0 ETH. The router's `pay()` function sees `address(this).balance = 1 ETH`, wraps it, and sends WETH to the pool. User B's own WETH is never touched.
- **User A permanently loses 1 ETH.** User B receives a free swap.
- The `refundETH()` escape hatch only helps if User A calls it before User B's swap executes. In a public mempool, a searcher can front-run `refundETH()` or simply submit a WETH swap to consume the balance first.

---

### Likelihood Explanation

Sending ETH with a non-WETH swap is a realistic user error — especially for users migrating from native-ETH DEX UIs or reusing scripts. The exploitation requires no privileged access: any ordinary WETH swap by any user passively drains the stranded balance. MEV bots monitoring the router's ETH balance can exploit this atomically.

---

### Recommendation

Add an input guard in every `payable` swap/liquidity entry-point:

```solidity
if (msg.value > 0 && params.tokenIn != WETH) revert ETHNotAcceptedForNonWETHSwap();
```

Alternatively, restrict `pay()` to use only the ETH that arrived in the **current** transaction by passing `msg.value` explicitly rather than reading `address(this).balance`.

---

### Proof of Concept

```
1. User A calls exactInputSingle({tokenIn: USDC, amountIn: 1000e6, ...})
   with msg.value = 1 ether.
   → USDC pulled from User A via safeTransferFrom; 1 ETH sits in router.

2. User B calls exactInputSingle({tokenIn: WETH, amountIn: 1e18, ...})
   with msg.value = 0.
   → Callback fires: pay(WETH, userB, pool, 1e18)
   → address(this).balance = 1e18 (User A's ETH)
   → nativeBalance >= value → WETH.deposit{value: 1e18}(); transfer to pool
   → User B's WETH never touched.

3. User A calls refundETH() → balance is 0; nothing returned.
   User A has lost 1 ETH with no recourse.
```

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```
