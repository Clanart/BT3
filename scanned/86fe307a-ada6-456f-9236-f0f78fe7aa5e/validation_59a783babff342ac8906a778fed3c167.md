The vulnerability is real. Here is the full analysis:

---

### Title
`refundETH` Drains Any Stranded ETH to Arbitrary Caller — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary

`refundETH()` unconditionally transfers the router's entire native ETH balance to `msg.sender` with no per-user accounting. Because `pay()` wraps only the exact ETH amount owed to the pool (leaving any excess on the contract), a user who overpays ETH on a payable swap call will have their surplus stolen by any address that calls `refundETH()` first.

### Finding Description

`refundETH()` contains no guard linking the refund to the caller's own deposit: [1](#0-0) 

`pay()`, when the token is WETH and native ETH is available, wraps **exactly** `value` wei — not the full balance: [2](#0-1) 

Any ETH above `value` remains on the contract after the swap callback returns. A second caller can then invoke `refundETH()` (directly or via `multicall`) and receive the entire residual balance.

`multicall` uses `delegatecall`, so `msg.sender` inside `refundETH` is the attacker's address, not the original depositor: [3](#0-2) 

The question's "partially-failed multicall" framing is slightly imprecise — `Address.functionDelegateCall` reverts the whole batch on failure, so ETH is returned in that case. The real stranding path is **excess ETH from overpayment on any payable swap entry point** (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput` are all `payable`).

### Impact Explanation

Direct loss of user ETH principal. Any ETH overpaid by User A (e.g., sending 2 ETH when 1 ETH is consumed by the swap) is immediately claimable by any address that calls `refundETH()` before User A does. There is no per-depositor ledger; the function sends `address(this).balance` in full to whoever calls it first.

### Likelihood Explanation

- ETH-in swaps are a primary use case for the router.
- Users routinely send a small ETH surplus to cover slippage or gas estimation error.
- MEV bots can monitor the mempool for payable swap calls and front-run the victim's own `refundETH` call (or simply call it in a subsequent block if the victim forgets).
- No special privilege or malicious pool is required — the attacker only needs to call a public function.

### Recommendation

Track per-caller ETH deposits in transient storage (EIP-1153) at the start of each payable entry point and restrict `refundETH` to return only the caller's own recorded deposit. Alternatively, enforce that `refundETH` can only be called within the same `multicall` batch as the originating swap (e.g., by checking a transient "active multicall initiator" slot).

### Proof of Concept

```solidity
// Foundry test sketch
function test_refundETH_steals_excess() public {
    // User A calls exactInputSingle with 2 ETH, swap only needs 1 ETH
    vm.deal(userA, 2 ether);
    vm.prank(userA);
    router.exactInputSingle{value: 2 ether}(params); // pay() wraps 1 ETH, 1 ETH stranded

    // User B calls refundETH and receives userA's 1 ETH
    uint256 before = userB.balance;
    vm.prank(userB);
    router.refundETH();
    assertEq(userB.balance - before, 1 ether); // B stole A's ETH
    assertEq(address(router).balance, 0);
}
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-77)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
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
