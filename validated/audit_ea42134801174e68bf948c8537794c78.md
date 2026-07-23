### Title
Unrestricted `sweepToken` and `unwrapWETH9` Allow Any Caller to Drain Router/Adder Token Balances — (`File: metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`sweepToken` and `unwrapWETH9` in `PeripheryPayments.sol` are `public payable` with no access control and accept an arbitrary `recipient` address. Any caller can invoke them at any time to redirect the entire ERC-20 or WETH balance of the router (or liquidity adder) to an attacker-controlled address. This is the direct payment-path analog of the reported burn-from-any-address pattern: instead of an owner destroying tokens from a victim's wallet, any unprivileged caller can drain tokens that belong to a user but are transiently held by the periphery contract.

---

### Finding Description

`PeripheryPayments` is inherited by both `MetricOmmSimpleRouter` and `MetricOmmPoolLiquidityAdder`. It exposes two unguarded public functions:

```solidity
// PeripheryPayments.sol L48-55
function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceToken = IERC20(token).balanceOf(address(this));
    if (balanceToken < amountMinimum) revert InsufficientToken(token, amountMinimum, balanceToken);
    if (balanceToken > 0) {
        IERC20(token).safeTransfer(recipient, balanceToken);
    }
}

// PeripheryPayments.sol L37-45
function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);
    if (balanceWETH > 0) {
        IWETH9(WETH).withdraw(balanceWETH);
        _transferETH(recipient, balanceWETH);
    }
}
```

Neither function checks `msg.sender` nor restricts `recipient`. The entire contract balance of any token (or WETH) is transferred to whoever supplies an attacker address.

Tokens legitimately land in the router/adder in several production flows:

1. **ETH overpayment in WETH swaps.** The `pay` function wraps only the exact `value` of ETH needed:
   ```solidity
   // PeripheryPayments.sol L74-77
   if (nativeBalance >= value) {
       IWETH9(WETH).deposit{value: value}();
       IERC20(WETH).safeTransfer(recipient, value);
   }
   ```
   Any ETH sent above `value` remains in the contract as raw ETH. A subsequent attacker call to `refundETH()` (which sends to `msg.sender`) drains it.

2. **Two-transaction WETH-unwrap pattern.** A user who does not use `multicall` may:
   - Tx 1: `exactInputSingle(ETH → WETH, recipient = router)` — WETH lands in the router.
   - Tx 2: `unwrapWETH9(minAmount, user)` — intended to unwrap and send to themselves.
   
   Between Tx 1 and Tx 2, an attacker calls `unwrapWETH9(0, attacker)` and steals all WETH.

3. **Intermediate tokens in multi-hop `exactInput`.** During `exactInput`, intermediate hops send output to `address(this)`:
   ```solidity
   // MetricOmmSimpleRouter.sol L106
   i == last ? params.recipient : address(this),
   ```
   Although this is within a single transaction, any revert-and-retry pattern or failed multicall step that leaves tokens in the router exposes them to `sweepToken`.

4. **`MetricOmmPoolLiquidityAdder` WETH liquidity.** Users adding WETH liquidity may send ETH; any surplus WETH held by the adder is equally exposed.

---

### Impact Explanation

Any ERC-20 token or WETH balance held by `MetricOmmSimpleRouter` or `MetricOmmPoolLiquidityAdder` can be stolen by an unprivileged caller in a single transaction. In the two-transaction WETH-unwrap scenario, the user's full swap output is lost. In the ETH-overpayment scenario, the excess ETH is lost. Both represent direct loss of user principal with no recovery path.

---

### Likelihood Explanation

- The two-transaction pattern is a realistic user mistake; many users interact with routers without batching via `multicall`.
- MEV bots routinely monitor for token balances in periphery contracts and sweep them within the same block.
- No special role, permit, or prior approval is required — a single external call suffices.

---

### Recommendation

Restrict `sweepToken` and `unwrapWETH9` so that `recipient` can only be `msg.sender`, eliminating the ability for a third party to redirect funds:

```solidity
function sweepToken(address token, uint256 amountMinimum) public payable {
    address recipient = msg.sender;
    uint256 balanceToken = IERC20(token).balanceOf(address(this));
    if (balanceToken < amountMinimum) revert InsufficientToken(token, amountMinimum, balanceToken);
    if (balanceToken > 0) IERC20(token).safeTransfer(recipient, balanceToken);
}

function unwrapWETH9(uint256 amountMinimum) public payable {
    address recipient = msg.sender;
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);
    if (balanceWETH > 0) { IWETH9(WETH).withdraw(balanceWETH); _transferETH(recipient, balanceWETH); }
}
```

This matches the pattern already used by `refundETH`, which correctly sends only to `msg.sender`.

---

### Proof of Concept

```
1. Alice calls exactInputSingle{value: 1.1 ether}(
       pool=WETH/USDC,
       tokenIn=WETH,
       amountIn=1 ether,
       recipient=address(router)   // intends to follow up with unwrapWETH9
   )
   → Pool receives 1 WETH; router holds 0.1 ETH excess + WETH output from pool.

2. Bob (attacker) observes Alice's pending Tx 2 (unwrapWETH9(1e18, alice)) in the mempool.

3. Bob front-runs with:
       router.unwrapWETH9(0, bob)
   → Router's entire WETH balance is unwrapped and sent to Bob.

4. Alice's Tx 2 executes but router WETH balance is now 0;
   if amountMinimum > 0 it reverts; if 0 it silently sends nothing.
   Alice loses her full swap output.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L37-55)
```text
  function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);

    if (balanceWETH > 0) {
      IWETH9(WETH).withdraw(balanceWETH);
      _transferETH(recipient, balanceWETH);
    }
  }

  /// @inheritdoc IPeripheryPayments
  function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceToken = IERC20(token).balanceOf(address(this));
    if (balanceToken < amountMinimum) revert InsufficientToken(token, amountMinimum, balanceToken);

    if (balanceToken > 0) {
      IERC20(token).safeTransfer(recipient, balanceToken);
    }
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }
```
