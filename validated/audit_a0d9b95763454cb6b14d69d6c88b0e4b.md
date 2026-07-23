The claim is valid. Here is the full analysis:

---

### Title
Unguarded `refundETH()` allows any caller to steal excess ETH left on the router by a victim who omitted `refundETH` from their multicall — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`refundETH()` unconditionally transfers the router's entire ETH balance to `msg.sender` with no access control. Because ETH sent as `msg.value` with a payable function call (e.g., `multicall{value: X}(...)`) bypasses the `receive()` guard, excess ETH from a user's swap can persist on the router between transactions. Any attacker can then call `refundETH()` in a separate transaction to drain it.

---

### Finding Description

`refundETH()` is implemented as:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
``` [1](#0-0) 

There is no check that `msg.sender` is the original depositor. It sends the full balance to whoever calls it.

The `receive()` guard only blocks **plain ETH transfers**:

```solidity
receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
}
``` [2](#0-1) 

It does **not** block ETH sent as `msg.value` alongside a function call. When a user calls `multicall{value: 1 ETH}(...)`, the ETH arrives via the `multicall` function's `payable` modifier — `receive()` is never invoked. The `pay()` function then wraps only the exact amount needed into WETH:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        ...
    }
``` [3](#0-2) 

Any ETH above the swap's exact input amount remains on the router as native ETH balance after the multicall returns. If the user did not include `refundETH` as a subsequent call in the same multicall, that ETH is stranded and unprotected.

The test suite itself confirms this pattern is expected and that `refundETH` must be included in the same multicall to recover excess ETH: [4](#0-3) 

---

### Impact Explanation

Direct loss of user ETH. Any attacker (or MEV bot) observing the mempool or the post-transaction state can call `refundETH()` and receive all stranded ETH. The victim receives nothing back. The loss equals the excess ETH the user sent above the swap's actual input amount.

---

### Likelihood Explanation

- The ETH-input multicall pattern is the documented and tested way to swap native ETH through this router.
- Users routinely send a round-number ETH value (e.g., 1 ETH) for a swap that consumes less (e.g., 0.5 ETH), relying on `refundETH` to recover the rest.
- Omitting `refundETH` from the multicall is a realistic user/integrator mistake, and MEV bots actively monitor for stranded ETH on routers.

---

### Recommendation

Change `refundETH()` to only refund to a caller-supplied `recipient` that is validated, **or** record the depositor address in transient storage at the start of `multicall` and enforce that only that address can call `refundETH` outside of a multicall context. The simplest safe fix matching Uniswap v3's own later hardening is to accept a `recipient` parameter and require the caller to explicitly name themselves:

```solidity
function refundETH(address recipient) external payable {
    uint256 balance = address(this).balance;
    if (balance > 0) _transferETH(recipient, balance);
}
```

This does not fully close the window (a griefing attacker could still pass the victim's address), so the stronger fix is to record `msg.sender` in transient storage at multicall entry and enforce it in `refundETH`.

---

### Proof of Concept

1. User calls `router.multicall{value: 1 ether}([exactInputSingle(tokenIn=WETH, amountIn=0.5 ether, ...)])` — no `refundETH` call included.
2. `pay()` wraps exactly 0.5 ETH into WETH and sends it to the pool. The remaining 0.5 ETH stays on the router.
3. Multicall returns. Router holds 0.5 ETH.
4. Attacker calls `router.refundETH()` in a separate transaction.
5. `refundETH` executes `_transferETH(msg.sender, 0.5 ether)` — attacker receives 0.5 ETH.
6. User's 0.5 ETH is permanently lost.

The existing payment test at line 74–85 of `MetricOmmSimpleRouter.payments.t.sol` already demonstrates that `refundETH` sends to any arbitrary `msg.sender` with no restriction: [5](#0-4)

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

**File:** metric-periphery/test/MetricOmmSimpleRouter.payments.t.sol (L74-85)
```text
  function test_refundETH_sendsBalanceToCaller() public {
    uint256 amount = 2 ether;
    vm.deal(address(router), amount);

    uint256 swapperBefore = swapper.balance;

    vm.prank(swapper);
    router.refundETH();

    assertEq(swapper.balance - swapperBefore, amount, "swapper refunded");
    assertEq(address(router).balance, 0, "router eth cleared");
  }
```
