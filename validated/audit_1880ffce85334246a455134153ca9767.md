### Title
Public `unwrapWETH9` with no caller binding drains entire router WETH balance to attacker-chosen recipient — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`unwrapWETH9` is `public payable` with no access control and no per-user accounting. It reads the router's entire WETH balance, withdraws it all via `IWETH9.withdraw`, and sends the resulting ETH to a caller-supplied `recipient`. Any address can call it at any time with `amountMinimum = 0` and redirect the full router WETH balance to themselves.

---

### Finding Description

The function body is:

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

There is no `msg.sender` check, no per-depositor ledger, and no minimum amount guard when `amountMinimum = 0`. The router is designed to hold WETH transiently — the intended pattern is `exactInputSingle(recipient=router)` followed by `unwrapWETH9` in the same `multicall`. But `unwrapWETH9` is independently callable between transactions, so any WETH that lands on the router in one transaction is exposed to theft before the victim's follow-up call.

The `multicall` dispatcher uses `delegatecall` into the same contract, so the WETH balance is shared across all callers and all steps: [2](#0-1) 

The intended two-step pattern (confirmed by the test suite) is: [3](#0-2) 

This pattern is safe only when both calls are in the same `multicall` transaction. When a user issues them as separate transactions — or when any WETH residue is left on the router from a prior partial/reverted flow — the balance is unprotected.

---

### Impact Explanation

Direct theft of user ETH output. An attacker calls `unwrapWETH9(0, attacker)` in a standalone transaction whenever the router holds a nonzero WETH balance. The entire balance is withdrawn and sent to the attacker. The victim receives nothing. Loss is 100% of the stranded WETH, with no floor — this meets the High threshold for direct principal loss.

---

### Likelihood Explanation

- The function is `public` with zero prerequisites.
- Any mempool observer can detect a pending `exactInputSingle(recipient=router)` transaction and front-run the victim's `unwrapWETH9` call.
- Even without front-running, any WETH residue left by a reverted or partial multicall step is permanently claimable by anyone.
- `sweepToken` has the identical issue for ERC-20 outputs: [4](#0-3) 

---

### Recommendation

Restrict `unwrapWETH9` (and `sweepToken`) so that only `msg.sender` can be the `recipient`, or enforce that `recipient == msg.sender`. Alternatively, track per-user WETH deposits in transient storage during the swap callback and only allow withdrawal of the amount attributed to the current caller. The `refundETH` function already does this correctly by sending only to `msg.sender`: [5](#0-4) 

---

### Proof of Concept

```solidity
// Foundry test sketch
function test_attacker_steals_victim_weth_output() public {
    // 1. Victim swaps token1 -> WETH, output goes to router
    vm.prank(victim);
    router.exactInputSingle(ExactInputSingleParams({
        ...,
        tokenOut: address(weth),
        recipient: address(router),   // WETH lands on router
        ...
    }));

    uint256 routerWeth = weth.balanceOf(address(router));
    assertGt(routerWeth, 0);

    // 2. Attacker front-runs victim's unwrapWETH9 call
    uint256 attackerEthBefore = attacker.balance;
    vm.prank(attacker);
    router.unwrapWETH9(0, attacker);   // amountMinimum=0, recipient=attacker

    // 3. Attacker receives victim's ETH; victim gets nothing
    assertEq(attacker.balance - attackerEthBefore, routerWeth);
    assertEq(weth.balanceOf(address(router)), 0);
}
```

### Citations

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L48-55)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
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
