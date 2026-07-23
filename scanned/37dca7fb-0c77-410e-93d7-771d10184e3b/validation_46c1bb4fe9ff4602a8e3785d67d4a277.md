The `refundETH()` implementation is fully confirmed. Let me verify the exact code path to ensure ETH can actually be stranded between transactions.

The vulnerability is confirmed. Here is the complete analysis:

---

### Title
Unprivileged caller can steal stranded ETH via `refundETH()` after a victim's payable multicall omits the refund step — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`refundETH()` is an unrestricted `external payable` function that transfers the router's entire native ETH balance to `msg.sender`. When a user sends excess ETH via a payable `multicall` without including a `refundETH()` call as the final step, the unused ETH is stranded on the router after the transaction. Any subsequent caller can invoke `refundETH()` in a separate transaction and receive all stranded ETH.

### Finding Description

`refundETH()` contains no access control: [1](#0-0) 

It unconditionally sends `address(this).balance` to `msg.sender`. The intended usage pattern is to include it as the last call inside a `multicall` batch so that unused ETH is returned to the original sender within the same transaction.

The `pay()` function consumes only the exact amount of native ETH needed for the swap when `token == WETH`: [2](#0-1) 

If `msg.value` exceeds `value`, only `value` wei is deposited into WETH and forwarded to the pool. The remainder stays on the router as raw ETH after the transaction completes.

The `receive()` fallback blocks direct ETH transfers from non-WETH addresses: [3](#0-2) 

However, `msg.value` attached to a payable function call (e.g., `multicall{value: 2e18}(...)`) bypasses `receive()` entirely — the ETH is credited to the contract's balance without triggering the fallback. This means excess ETH from a payable multicall is silently retained on the router after the transaction.

The `multicall` dispatcher is `payable` and uses `delegatecall`, so `msg.value` is available to all sub-calls: [4](#0-3) 

### Impact Explanation

A victim who calls `multicall{value: 2e18}([exactInputSingle(amountIn=1e18, tokenIn=WETH)])` without appending `refundETH()` will have 1e18 ETH stranded on the router after their transaction. An attacker monitoring the mempool or the router's ETH balance can immediately call `refundETH()` in the next block and receive the full stranded balance. This is a direct, irreversible loss of the victim's ETH principal with no protocol-level recovery path.

### Likelihood Explanation

The design documentation explicitly states that `refundETH` must be included in the same multicall: [5](#0-4) 

Any integrator, wallet, or user who constructs a multicall with excess ETH but omits the refund step — a common mistake — creates an immediately exploitable condition. The attack requires no special privileges, no malicious pool, and no non-standard token behavior.

### Recommendation

Restrict `refundETH()` so it can only be called as part of a `multicall` by tracking the original `msg.sender` in transient storage at `multicall` entry and checking it inside `refundETH()`. Alternatively, record the initiating address in transient storage and only allow that address to receive the refund. A simpler mitigation is to add a `recipient` parameter to `refundETH()` and require callers to explicitly specify the destination, though this alone does not prevent theft — the core fix must be an access control check binding the refund to the original multicall initiator.

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_attacker_steals_stranded_eth() public {
    uint128 amountIn = 1e18;
    uint256 msgValue = 2e18;

    // Victim sends 2 ETH but only 1 ETH is consumed; no refundETH() included
    vm.prank(victim);
    bytes[] memory calls = new bytes[](1);
    calls[0] = abi.encodeWithSelector(
        router.exactInputSingle.selector,
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(weth),
            tokenOut: address(token1),
            zeroForOne: true,
            amountIn: amountIn,
            amountOutMinimum: 0,
            recipient: victim,
            deadline: block.timestamp + 60,
            priceLimitX64: 0,
            extensionData: ""
        })
    );
    router.multicall{value: msgValue}(calls);

    // 1e18 ETH is now stranded on the router
    assertEq(address(router).balance, 1e18);

    // Attacker calls refundETH() in the next transaction
    uint256 attackerBefore = attacker.balance;
    vm.prank(attacker);
    router.refundETH();

    // Attacker received the victim's stranded ETH
    assertEq(attacker.balance - attackerBefore, 1e18);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
  }
```

**File:** metric-periphery/contracts/interfaces/IMetricOmmPoolLiquidityAdder.sol (L16-17)
```text
///      `multicall{value}`) when the pool's WETH leg is token0 or token1; unused ETH can be reclaimed via
///      `refundETH` in the same multicall.
```
