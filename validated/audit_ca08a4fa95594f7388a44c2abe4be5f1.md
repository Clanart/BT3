### Title
Leftover ETH on Router Subsidizes Attacker's WETH Swap via `PeripheryPayments.pay()` Partial-ETH Branch — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` uses `address(this).balance` — the router's **total** ETH balance — as a subsidy for any WETH swap. Because `multicall` is `payable` and excess ETH is never automatically refunded, a prior user's unclaimed ETH persists on the router across transactions. A subsequent attacker calling `exactInputSingle` with `tokenIn=WETH` and `amountIn > address(router).balance` hits the partial-ETH branch, which consumes the prior user's ETH to cover part of the attacker's swap input, reducing the WETH pulled from the attacker by exactly that amount. The prior user suffers a direct ETH principal loss.

---

### Finding Description

`PeripheryPayments.pay()` contains three branches for WETH payment: [1](#0-0) 

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
    } else if (nativeBalance > 0) {                          // ← partial branch
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
```

The partial branch (lines 78–81) deposits **all** ETH currently on the router and reduces the `safeTransferFrom` pull from the current payer by that amount. There is no ownership check — any ETH on the router, regardless of who deposited it, is consumed.

**How ETH accumulates between transactions:**

The `receive()` guard only blocks bare ETH transfers: [2](#0-1) 

When ETH is sent *with calldata* (e.g., `multicall{value: X}(...)`), `receive()` is never invoked — the ETH is silently credited to the router's balance. The `pay()` function's `nativeBalance >= value` branch deposits only the exact `value` needed, leaving any excess on the router: [3](#0-2) 

If the user omits `refundETH()` from their multicall, the surplus ETH persists until the next transaction.

The `multicall` entry point is `payable` and imposes no automatic refund: [4](#0-3) 

---

### Impact Explanation

**Direct ETH principal loss for the prior user.** Concretely:

- User A calls `multicall{value: 1 ETH}([exactInputSingle(tokenIn=WETH, amountIn=0.5 ETH)])` without `refundETH()`. The `pay()` full branch deposits 0.5 ETH; 0.5 ETH remains on the router.
- Attacker B calls `exactInputSingle(tokenIn=WETH, amountIn=1 ETH)` (no ETH sent). The partial branch deposits User A's 0.5 ETH into WETH, transfers it to the pool, then pulls only 0.5 ETH of WETH from Attacker B.
- Result: Attacker B receives a 1 ETH-equivalent swap while paying only 0.5 ETH. User A's 0.5 ETH is permanently consumed.

The pool receives the correct input (1 ETH worth of WETH), so pool accounting is intact — the loss falls entirely on User A.

---

### Likelihood Explanation

The prerequisite is a user omitting `refundETH()` from a native-ETH multicall. This is a realistic and common mistake — the protocol provides no enforcement, and the pattern is opt-in. MEV bots can monitor the router's ETH balance on-chain and immediately exploit any nonzero balance in the next block. The attack requires no special permissions, no malicious pool, and no non-standard tokens.

---

### Recommendation

Remove the partial-ETH subsidy logic entirely. The router should never silently consume ETH that was not sent in the current transaction. Two safe alternatives:

1. **Track per-call ETH**: Record `msg.value` in transient storage at the top of each swap entry point and use only that amount in `pay()`, reverting if it is insufficient.
2. **Eliminate the partial branch**: Only use the router's ETH balance when `msg.value` was explicitly provided in the current call (i.e., `msg.value >= value`); otherwise fall through to `safeTransferFrom`.

Additionally, consider adding an automatic `refundETH()` at the end of each swap entry point, or documenting and enforcing the multicall pattern at the ABI level.

---

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_leftoverEthSubsidizesAttacker() public {
    uint256 amountIn = 1 ether;
    uint256 excess   = 0.5 ether;

    // User A: sends 1.5 ETH but only swaps 1 ETH worth; forgets refundETH()
    vm.deal(userA, amountIn + excess);
    vm.prank(userA);
    bytes[] memory callsA = new bytes[](1);
    callsA[0] = abi.encodeWithSelector(
        router.exactInputSingle.selector,
        ExactInputSingleParams({ tokenIn: address(weth), amountIn: amountIn, ... })
    );
    router.multicall{value: amountIn + excess}(callsA); // 0.5 ETH left on router

    assertEq(address(router).balance, excess, "leftover ETH on router");

    // Attacker B: swaps 1.5 ETH worth of WETH, but only pays 1 ETH (router covers 0.5)
    uint256 attackAmountIn = amountIn + excess; // 1.5 ETH
    deal(address(weth), attacker, attackAmountIn - excess); // only 1 ETH of WETH
    vm.prank(attacker);
    weth.approve(address(router), attackAmountIn);
    router.exactInputSingle(
        ExactInputSingleParams({ tokenIn: address(weth), amountIn: attackAmountIn, ... })
    );

    // Assert: router's ETH is gone (User A's 0.5 ETH consumed)
    assertEq(address(router).balance, 0, "router drained");
    // Assert: attacker's WETH balance reduced by only 1 ETH, not 1.5 ETH
    assertEq(weth.balanceOf(attacker), 0, "attacker paid only 1 ETH for 1.5 ETH swap");
}
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
  }
```
