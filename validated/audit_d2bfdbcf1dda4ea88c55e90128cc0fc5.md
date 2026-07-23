The code is clear. Let me confirm the exact flow before writing the finding.

**`refundETH()` — no access control, sends to `msg.sender`:** [1](#0-0) 

**`pay()` — only wraps exactly `value` ETH, leaving any excess on the router:** [2](#0-1) 

**`multicall()` — payable, delegates calls, no ETH accounting between calls:** [3](#0-2) 

**`receive()` — blocks direct ETH transfers but does NOT block `msg.value` in payable calls:** [4](#0-3) 

The attack path is real and complete:

1. Victim calls `exactInputSingle` (payable) with `msg.value = 2e18`, `amountIn = 1e18`, `tokenIn = WETH`.
2. `pay()` wraps exactly `value` (1 ETH) and transfers it to the pool. The remaining 1 ETH sits on the router at end of transaction.
3. Attacker calls `refundETH()` in the next transaction. Since there is no access control and it sends `address(this).balance` to `msg.sender`, the attacker receives the victim's 1 ETH.

The `receive()` guard is irrelevant here — it only blocks `address(router).call{value:...}("")` from non-WETH senders. ETH sent as `msg.value` to any `payable` function bypasses `receive()` entirely and lands on the contract.

The existing test `test_refundETH_sendsBalanceToCaller` confirms this behavior explicitly — it deals ETH to the router and shows any caller gets it back. [5](#0-4) 

---

### Title
Unprivileged caller can steal stranded ETH via `refundETH()` after a victim's payable swap with excess `msg.value` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`PeripheryPayments.refundETH()` is an unrestricted external function that transfers the router's entire ETH balance to `msg.sender`. When a user calls a payable swap function (e.g., `exactInputSingle`) with `msg.value` exceeding `amountIn` for a WETH swap and omits `refundETH()` from their multicall, the excess ETH is left on the router after the transaction. Any subsequent caller can invoke `refundETH()` to drain it.

### Finding Description
`refundETH()` contains no check binding the refund to the original depositor:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);  // sends to whoever calls, not original sender
    }
}
```

The `pay()` function, when `tokenIn == WETH`, wraps exactly `value` (the swap's `amountIn`) from the contract's native balance and transfers it to the pool. Any `msg.value` above `amountIn` is not consumed and remains on the router after the transaction ends. Because `refundETH()` is a standalone external function callable in any transaction, a third party can claim it.

### Impact Explanation
Direct loss of ETH principal for the victim. The attacker receives ETH they did not deposit. Impact is proportional to the excess `msg.value` sent. This meets the Critical/High threshold for direct loss of user principal.

### Likelihood Explanation
Moderate. The pattern of sending excess ETH with a WETH swap (e.g., to avoid a separate WETH approval) is common in Uniswap-style routers. Users who forget to append `refundETH()` to their multicall, or who call `exactInputSingle` directly with excess `msg.value`, are vulnerable. The attack requires only watching the mempool or chain state for a router ETH balance and calling `refundETH()`.

### Recommendation
Bind the refund to the original depositor using transient storage. Record `msg.sender` at the start of each payable entry point and restrict `refundETH()` to that stored address, clearing it after use. Alternatively, automatically refund excess ETH at the end of each swap entry point rather than relying on the caller to include `refundETH()` in a multicall.

### Proof of Concept
```solidity
// Foundry integration test (pseudo-code)
function test_attacker_steals_excess_eth() public {
    address victim  = address(0xA);
    address attacker = address(0xB);
    vm.deal(victim, 2 ether);

    // Victim swaps 1 ETH worth of WETH but sends 2 ETH, omitting refundETH()
    vm.prank(victim);
    router.exactInputSingle{value: 2 ether}(
        ExactInputSingleParams({
            pool: address(wethPool),
            tokenIn: address(weth),
            tokenOut: address(token1),
            zeroForOne: true,
            amountIn: 1 ether,
            amountOutMinimum: 0,
            recipient: victim,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    );
    // 1 ETH is now stranded on the router

    assertEq(address(router).balance, 1 ether);

    // Attacker claims it
    uint256 before = attacker.balance;
    vm.prank(attacker);
    router.refundETH();

    assertEq(attacker.balance - before, 1 ether); // attacker stole victim's ETH
    assertEq(address(router).balance, 0);
}
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
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
