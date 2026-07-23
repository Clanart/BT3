### Title
Unattributed native ETH balance in `pay()` lets any caller drain stranded ETH from prior users - (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` settles WETH swaps by reading `address(this).balance` — the router's **total** native ETH balance — with no per-user attribution. Any ETH left on the router by a prior caller (e.g., from a `multicall{value: X}` that omits `refundETH()`) is silently consumed by the next caller's WETH swap, or stolen outright via the unrestricted `refundETH()` helper.

---

### Finding Description

`pay()` in `PeripheryPayments.sol` handles the `token == WETH` branch as follows: [1](#0-0) 

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // ← total router balance, not msg.value
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
}
```

`address(this).balance` is the router's **aggregate** native balance, not the ETH the current caller sent. ETH accumulates on the router whenever a payable entry point (`multicall`, `exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`) receives more `msg.value` than the swap consumes and the caller omits `refundETH()`.

The test suite itself documents this stranding risk: [2](#0-1) 

The test sends `2 ether` but only uses `1_000` wei; it explicitly adds `refundETH()` as the second call to recover the remainder. Without that second call, `2 ether - 1_000` stays on the router indefinitely.

`refundETH()` has no access control and sends the router's **entire** ETH balance to `msg.sender`: [3](#0-2) 

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);   // ← no caller check
    }
}
```

The same `pay()` function is inherited by `MetricOmmPoolLiquidityAdder`, so the same stranding and theft path exists for WETH-denominated liquidity additions: [4](#0-3) 

---

### Impact Explanation

**Direct loss of user principal.** A victim's stranded ETH is transferred to the attacker with no protocol-level barrier. Two theft paths exist:

1. **Direct drain** — attacker calls `refundETH()` and receives the entire router ETH balance.
2. **Free swap** — attacker calls `exactInputSingle(tokenIn=WETH, amountIn=X)` with zero ETH and zero WETH approval; `pay()` deposits the victim's stranded ETH as WETH and settles the swap, giving the attacker full swap output at zero cost.

Loss magnitude equals the stranded ETH amount, which can be arbitrarily large (e.g., a user who sends `amountInMaximum` ETH for an exact-output swap and omits `refundETH()`).

---

### Likelihood Explanation

- `exactOutputSingle` and `exactOutput` are `payable` and commonly called with excess ETH for slippage headroom. If the caller does not wrap the call in a `multicall` that ends with `refundETH()`, the surplus is stranded.
- `multicall` itself is `payable`; any sub-call that uses less than the forwarded `msg.value` leaves a residue.
- The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) prevents accidental ETH deposits but does not prevent stranding via `msg.value` on payable functions.
- An attacker can watch the mempool for transactions that strand ETH and immediately call `refundETH()` in the next block, or sandwich the victim's transaction.
- No special role or privileged access is required.

---

### Recommendation

Track per-transaction ETH attribution in transient storage. At the start of each payable entry point, record `msg.value` in a transient slot. In `pay()`, consume only from that recorded budget rather than from `address(this).balance`. Zero the slot before returning. This ensures each caller's ETH is isolated and cannot be consumed by a subsequent caller.

Alternatively, enforce that `refundETH()` is always the final step of any payable multicall by reverting if the router holds a non-zero ETH balance at the end of `multicall` (similar to a post-condition check).

---

### Proof of Concept

```solidity
// Step 1 – Alice strands ETH (omits refundETH)
router.multicall{value: 2 ether}(
    [abi.encodeWithSelector(router.exactInputSingle.selector,
        ExactInputSingleParams({
            tokenIn: WETH, amountIn: 1_000, ...
        })
    )]
    // No refundETH() call — ~2 ether - 1_000 wei remains on router
);

// Step 2a – Bob steals via refundETH (zero cost)
router.refundETH();   // Bob receives ~2 ether - 1_000 wei

// Step 2b – Alternatively, Bob gets a free swap
// Bob has 0 ETH sent, 0 WETH approved
router.exactInputSingle(ExactInputSingleParams({
    tokenIn: WETH,
    amountIn: 1 ether,   // ≤ stranded balance
    recipient: bob,
    ...
}));
// pay() sees address(this).balance >= 1 ether, deposits Alice's ETH,
// Bob receives swap output at zero personal cost
```

### Citations

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

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L106-133)
```text
  function test_multicall_ethInput_exactInputSingle_refundsUnusedEth() public {
    uint128 amountIn = 1_000;
    uint256 msgValue = 2 ether;
    uint256 swapperEthBefore = swapper.balance;

    vm.prank(swapper);
    bytes[] memory calls = new bytes[](2);
    calls[0] = abi.encodeWithSelector(
      router.exactInputSingle.selector,
      IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: amountIn,
        amountOutMinimum: 0,
        recipient: recipient,
        deadline: _deadline(),
        priceLimitX64: 0,
        extensionData: ""
      })
    );
    calls[1] = abi.encodeWithSelector(router.refundETH.selector);
    router.multicall{value: msgValue}(calls);

    assertEq(swapper.balance, swapperEthBefore - amountIn, "unused eth refunded");
    _assertRouterEmpty();
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L172-177)
```text
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
```
