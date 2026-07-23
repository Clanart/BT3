### Title
Unprotected `unwrapWETH9` allows any caller to steal all WETH stranded on the router as ETH — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`unwrapWETH9` is a `public payable` function with no access control. It reads the router's entire WETH balance, withdraws it all, and sends it to a fully attacker-controlled `recipient`. Any WETH that reaches the router contract and is not consumed in the same atomic transaction is immediately stealable by any third party.

---

### Finding Description

The function implementation is:

```solidity
function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);
    if (balanceWETH > 0) {
        IWETH9(WETH).withdraw(balanceWETH);
        _transferETH(recipient, balanceWETH);
    }
}
``` [1](#0-0) 

There is no `msg.sender` check, no ownership check, and no per-caller accounting. The function sweeps **the entire router WETH balance** to a caller-supplied address.

The intended usage pattern — demonstrated in the test suite — is to compose a swap with `recipient: address(router)` followed immediately by `unwrapWETH9` inside a single `multicall`: [2](#0-1) 

This pattern is safe only when both steps are atomic. However, the router's `exactInputSingle`, `exactInput`, and `exactOutput` functions all accept `recipient: address(router)` as a valid parameter in a **standalone call** (outside of multicall). If a user calls a swap with `recipient: address(router)` in one transaction and does not atomically follow it with `unwrapWETH9`, the WETH is stranded on the router across a transaction boundary. [1](#0-0) 

Once stranded, any attacker can call:

```solidity
router.unwrapWETH9(0, attacker);
```

with `amountMinimum = 0` (bypassing the only guard) and receive the full WETH balance as ETH. The victim receives nothing.

---

### Impact Explanation

Direct theft of user principal. The attacker receives 100% of the stranded WETH as ETH. The victim's funds are permanently lost. This meets the Sherlock High threshold for direct loss of user principal.

---

### Likelihood Explanation

The stranding precondition is realistic:
1. The router's swap functions publicly accept `recipient: address(router)` — this is the documented pattern for the WETH-unwrap flow.
2. A user who calls a swap standalone (not via multicall) with `recipient: address(router)` strands WETH between transactions.
3. WETH can also be stranded by direct ERC-20 `transfer` to the router (the `receive()` guard only blocks native ETH, not WETH token transfers).
4. An attacker can monitor the mempool or simply poll the router's WETH balance and call `unwrapWETH9(0, attacker)` at any time. [3](#0-2) 

---

### Recommendation

Restrict `unwrapWETH9` (and `sweepToken`) so that only `msg.sender` can be the `recipient`, or require `recipient == msg.sender`. Alternatively, track per-caller WETH deposits in transient storage and only allow each caller to withdraw their own attributed balance. The `refundETH` function already correctly uses `msg.sender` as the recipient — the same pattern should be applied to `unwrapWETH9` and `sweepToken`. [4](#0-3) 

---

### Proof of Concept

1. **Strand WETH**: Victim calls `router.exactInputSingle(...)` with `recipient: address(router)` as a standalone transaction (not inside a multicall). The swap completes; WETH lands on the router.
2. **Steal**: Attacker calls `router.unwrapWETH9(0, attacker)` from any EOA.
3. **Result**: `IWETH9(WETH).withdraw(balanceWETH)` converts all router WETH to ETH; `_transferETH(attacker, balanceWETH)` sends it to the attacker. Victim receives zero. The router WETH and ETH balances are both zero.

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L37-45)
```text
  function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);

    if (balanceWETH > 0) {
      IWETH9(WETH).withdraw(balanceWETH);
      _transferETH(recipient, balanceWETH);
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

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L135-162)
```text
  function test_multicall_tokenForWeth_thenUnwrapEth() public {
    uint128 amountIn = 3_000;
    uint256 recipientEthBefore = recipient.balance;

    vm.prank(swapper);
    bytes[] memory calls = new bytes[](2);
    calls[0] = abi.encodeWithSelector(
      router.exactInputSingle.selector,
      IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token1),
        tokenOut: address(weth),
        zeroForOne: false,
        amountIn: amountIn,
        amountOutMinimum: 0,
        recipient: address(router),
        deadline: _deadline(),
        priceLimitX64: type(uint128).max,
        extensionData: ""
      })
    );
    calls[1] = abi.encodeWithSelector(router.unwrapWETH9.selector, uint256(0), recipient);
    router.multicall(calls);

    assertGt(recipient.balance, recipientEthBefore, "recipient received eth");
    assertEq(weth.balanceOf(address(router)), 0, "router weth cleared");
    assertEq(address(router).balance, 0, "router eth cleared");
  }
```
