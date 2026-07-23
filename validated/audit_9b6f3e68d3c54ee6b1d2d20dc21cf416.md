### Title
Unrestricted `sweepToken` and `unwrapWETH9` Allow Any Caller to Steal Router-Held Tokens — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`sweepToken` and `unwrapWETH9` in `PeripheryPayments.sol` are `public payable` with **no access control** and accept a caller-chosen `recipient`. They drain the router's **entire** balance of any token or WETH to any address. Because the `exactInput` multi-hop path intentionally routes intermediate output through `address(this)` between hops, and because users legitimately set `recipient = address(router)` before calling `unwrapWETH9`, tokens can realistically be stranded on the router in a state that any attacker can immediately sweep.

---

### Finding Description

`PeripheryPayments.sweepToken` and `unwrapWETH9` are declared `public payable` with no `msg.sender` guard and no per-user accounting:

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
``` [1](#0-0) 

Both functions transfer the router's **full** balance to a caller-supplied `recipient` with `amountMinimum = 0` accepted. There is no check that `msg.sender` is the depositor, no per-user balance ledger, and no restriction on `recipient`.

The `exactInput` multi-hop path explicitly routes intermediate output through the router:

```solidity
// MetricOmmSimpleRouter.sol L103-106
_setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
...
i == last ? params.recipient : address(this),   // recipient per hop
``` [2](#0-1) 

The comment on `exactInput` confirms: *"Intermediate tokens stay on this contract; the final hop sends output to `recipient`."* [3](#0-2) 

Additionally, the `pay()` helper for WETH deposits only the exact swap amount from `msg.value`, leaving any excess native ETH on the router:

```solidity
// PeripheryPayments.sol L75-77
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);
}
``` [4](#0-3) 

The residual `nativeBalance - value` ETH stays on the router. `refundETH()` sends to `msg.sender` (the attacker, not the original depositor):

```solidity
// PeripheryPayments.sol L58-63
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
``` [5](#0-4) 

---

### Impact Explanation

**Direct loss of user principal.** Any attacker can call `sweepToken(token, 0, attacker)` or `unwrapWETH9(0, attacker)` to drain the router's entire balance of any token or WETH to an arbitrary address. Similarly, `refundETH()` sends the full native ETH balance to `msg.sender`, so any caller can claim ETH that belongs to a different user. This is a High-severity direct theft of user funds with no privileged precondition.

---

### Likelihood Explanation

**Medium-High.** The two most realistic stranding paths are:

1. **WETH-output swap with `recipient = address(router)`**: A user calls `exactInput` or `exactInputSingle` with WETH as the output token and sets `recipient = address(router)` intending to call `unwrapWETH9` in a subsequent transaction (not a multicall). WETH lands on the router. An attacker front-runs the `unwrapWETH9` call.

2. **Excess ETH from exact-output WETH swap**: A user calls `exactOutputSingle` with WETH as input and sends `msg.value = amountInMaximum`. The actual `amountIn < msg.value`; the excess ETH stays on the router. If the user does not call `refundETH()` in the same multicall, an attacker calls `refundETH()` and receives the ETH.

Both paths require no privileged access and are reachable by any external caller.

---

### Recommendation

Add a `msg.sender`-bound recipient restriction or per-user balance accounting. The minimal fix mirrors the `closeLoan()` recommendation — restrict who may trigger the drain:

```solidity
// Option A: restrict recipient to msg.sender
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

Alternatively, maintain a per-user transient balance ledger so only the depositor can sweep their own residue.

---

### Proof of Concept

```
// Attack scenario: steal WETH stranded by a user's two-transaction flow

// Tx 1 — Victim
router.exactInputSingle(ExactInputSingleParams({
    pool:            WETH_USDC_POOL,
    tokenIn:         USDC,
    recipient:       address(router),   // intends to unwrap in next tx
    amountIn:        1000e6,
    amountOutMinimum: 0,
    zeroForOne:      false,
    priceLimitX64:   0,
    deadline:        block.timestamp + 60,
    extensionData:   ""
}));
// WETH now sits on the router

// Tx 2 — Attacker (front-runs victim's unwrapWETH9 call)
router.unwrapWETH9(0, attacker);
// Attacker receives all WETH as ETH; victim receives nothing
```

The same pattern applies to `sweepToken` for any ERC-20 and to `refundETH` for excess native ETH left from an exact-output WETH swap.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L89-91)
```text
  /// @dev Walks `pools[0..n-1]` forward. Each hop swaps a positive `amountSpecified`; the prior hop's output
  ///      becomes the next hop's input. Intermediate tokens stay on this contract; the final hop sends output to
  ///      `recipient`.
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-106)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
```
