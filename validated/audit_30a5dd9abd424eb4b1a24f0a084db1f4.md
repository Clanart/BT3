Looking at the code, I need to verify:
1. Whether `refundETH()` has any access control
2. Whether ETH can realistically accumulate on the router between transactions
3. Whether the `receive()` guard prevents the attack path

**`refundETH()` — no access control:** [1](#0-0) 

The function unconditionally sends `address(this).balance` to `msg.sender`. No check that `msg.sender` is the original depositor.

**`receive()` guard — only blocks plain ETH transfers:** [2](#0-1) 

This only fires on bare ETH sends (no calldata). It does **not** prevent ETH from accumulating via `msg.value` on `payable` functions.

**ETH stranding path — `pay()` wraps only the needed amount:** [3](#0-2) 

When `token == WETH` and `nativeBalance >= value`, exactly `value` ETH is wrapped and forwarded. The remainder (`nativeBalance - value`) stays on the contract after the transaction ends.

**The router's swap functions are all `payable`:** [4](#0-3) [5](#0-4) 

A user who calls `exactInputSingle` with `msg.value = 2 ETH` but `amountIn = 1 ETH` (tokenIn = WETH) will have 1 ETH stranded on the router after the transaction. There is no automatic refund.

---

### Title
Unguarded `refundETH()` allows any caller to steal ETH stranded on the router from prior over-funded swap calls — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`PeripheryPayments.refundETH()` sends the entire native ETH balance of the router to `msg.sender` with no access control. Because all swap entry points are `payable` and `pay()` wraps only the exact amount needed, excess ETH sent with a swap persists on the router after the transaction. Any EOA can then call `refundETH()` in a subsequent transaction to drain it.

### Finding Description
`refundETH()` is designed to be used inside a `multicall` batch (e.g., `[exactInputSingle(...), refundETH()]`) so that excess ETH is returned to the caller within the same transaction. However, nothing enforces this pattern. If a user calls a swap function directly with excess ETH — or omits `refundETH()` from their multicall — the surplus ETH remains on the router after the transaction settles. The `receive()` guard only blocks bare ETH transfers; it does not prevent accumulation via `msg.value` on `payable` functions. Once ETH is stranded, any address can call `refundETH()` and receive the full balance.

The concrete stranding path:
1. User calls `exactInputSingle{value: 2 ether}(...)` with `tokenIn = WETH`, `amountIn = 1 ether`.
2. `pay()` wraps and forwards exactly 1 ETH; 1 ETH remains on the router.
3. Transaction ends; 1 ETH is now permanently accessible to anyone.
4. Attacker calls `refundETH()` and receives 1 ETH.

### Impact Explanation
Direct loss of user principal. Any ETH left on the router — from over-funded swaps or failed multicall compositions — is immediately claimable by an arbitrary third party. The victim receives nothing.

### Likelihood Explanation
Users interacting with WETH-input swaps via ETH are expected to bundle `refundETH()` in a multicall, but this is not enforced. Wallets, aggregators, or users calling swap functions directly without a multicall wrapper will routinely strand ETH. A MEV bot monitoring the router's ETH balance can atomically steal it in the next block.

### Recommendation
Restrict `refundETH()` to only be callable within a `multicall` context (e.g., via a reentrancy-style flag set at multicall entry), or record the original `msg.sender` at multicall entry in transient storage and require `msg.sender == originalCaller` inside `refundETH()`. Alternatively, automatically refund excess ETH at the end of each swap function rather than relying on a separate call.

### Proof of Concept
```solidity
// Foundry test sketch
function test_refundETH_theft() public {
    address victim = makeAddr("victim");
    address attacker = makeAddr("attacker");
    vm.deal(victim, 2 ether);

    // Victim swaps with excess ETH, forgets refundETH
    vm.prank(victim);
    router.exactInputSingle{value: 2 ether}(
        ExactInputSingleParams({..., tokenIn: WETH, amountIn: 1 ether, ...})
    );
    // 1 ETH is now stranded on router

    assertEq(address(router).balance, 1 ether);

    // Attacker drains it
    vm.prank(attacker);
    router.refundETH();

    assertEq(attacker.balance, 1 ether);
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-78)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-92)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
```
