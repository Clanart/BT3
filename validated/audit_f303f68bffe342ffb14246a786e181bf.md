The vulnerability is real. Here is the complete analysis:

---

### Title
Unguarded `refundETH()` Allows Any Caller to Steal Excess ETH Stranded by a Prior Victim's Multicall — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`refundETH()` transfers the router's entire native ETH balance to `msg.sender` with no access control and no attribution to who deposited the ETH. The `pay()` function intentionally wraps only exactly `amountIn` ETH when settling a WETH swap, leaving any `msg.value` surplus on the router. A victim who calls `multicall{value: V}([exactInputSingle({tokenIn: WETH, amountIn: A})])` with `V > A` and no trailing `refundETH` step strands `V - A` ETH on the router permanently. Any subsequent caller can steal it by calling `refundETH()`.

---

### Finding Description

**Step 1 — ETH enters the router via `multicall`.**

`multicall` is `public payable` and performs no accounting of `msg.value`: [1](#0-0) 

**Step 2 — `pay()` wraps only `value` (= `amountIn`) ETH, leaving the surplus.**

When `token == WETH` and `nativeBalance >= value`, the function deposits exactly `value` into WETH and transfers it to the pool. The remaining `nativeBalance - value` stays as raw ETH on the router: [2](#0-1) 

**Step 3 — `refundETH()` has no access control.**

It sends `address(this).balance` — the entire router ETH balance — to `msg.sender`, whoever that is: [3](#0-2) 

There is no check that `msg.sender` is the address that originally deposited the ETH. Any caller in any subsequent transaction receives the full balance.

**Step 4 — `receive()` does not prevent stranding.**

The `receive()` guard only blocks direct ETH transfers from non-WETH addresses. It does not prevent ETH from accumulating via `msg.value` in `payable` functions: [4](#0-3) 

---

### Impact Explanation

Direct, permanent loss of user ETH. Once the victim's transaction completes, the stranded ETH is immediately claimable by any address. The attacker does not need to be in the same transaction, does not need any special role, and does not need to front-run — they can call `refundETH()` at any time after the victim's transaction is mined. Loss is bounded only by how much excess ETH the victim sent.

---

### Likelihood Explanation

The correct pattern — always appending `refundETH` as the last multicall step when sending excess ETH — is shown in the test suite but is never enforced by the contract. Users who send a round ETH amount (e.g., `1 ether`) for a swap that costs less, or who reuse a frontend that omits the refund step, will strand ETH. The attack requires only a single public call with no preconditions.

---

### Recommendation

Two complementary fixes:

1. **Track the depositor in transient storage** at the start of `multicall` and restrict `refundETH` to return ETH only to that address within the same transaction context.
2. **Auto-refund at the end of `multicall`**: after all delegatecalls complete, if `address(this).balance > 0`, transfer it back to `msg.sender` unconditionally.

Option 2 is simpler and matches the Uniswap v3 `SwapRouter02` approach.

---

### Proof of Concept

```solidity
// Foundry test sketch (production router + real pool)
function test_strandedEthStolenByAttacker() public {
    uint128 amountIn = 90 ether;
    uint256 msgValue = 100 ether;
    vm.deal(victim, msgValue);

    // Victim multicalls WITHOUT a trailing refundETH step
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
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    );
    vm.prank(victim);
    router.multicall{value: msgValue}(calls);

    // 10 ETH is now stranded on the router
    assertEq(address(router).balance, 10 ether);

    // Attacker steals it in a subsequent transaction
    uint256 attackerBefore = attacker.balance;
    vm.prank(attacker);
    router.refundETH();
    assertEq(attacker.balance - attackerBefore, 10 ether);
    assertEq(address(router).balance, 0);
}
```

The `pay()` call wraps exactly `amountIn = 90 ether` into WETH and sends it to the pool. [5](#0-4) 
The remaining 10 ETH sits on the router until `refundETH()` is called by anyone. [6](#0-5)

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
  }
```

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-77)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
```
