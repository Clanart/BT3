The code is clear. Let me trace the exact path.

### Title
Unrestricted `refundETH()` Allows Any Caller to Steal Excess ETH Left on the Router — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`refundETH()` sends the router's entire ETH balance to `msg.sender` with no access control. Because `pay()` wraps only the exact ETH needed for a swap and leaves any surplus on the contract, a user who omits `refundETH()` from their `multicall` leaves recoverable ETH on the router. Any third party can immediately call `refundETH()` in a subsequent transaction and receive that ETH.

---

### Finding Description

`refundETH()` is unconditionally open: [1](#0-0) 

There is no check that `msg.sender` was the original depositor, no transient-storage ownership record, and no per-user accounting. Whoever calls it first receives everything.

ETH accumulates on the router because `pay()` wraps only the exact amount required by the swap and leaves the remainder as raw ETH: [2](#0-1) 

`multicall()` uses `delegatecall`, so `msg.value` is shared across all sub-calls in a single transaction: [3](#0-2) 

The `receive()` guard (which rejects non-WETH ETH) does not apply here because ETH sent as `msg.value` to a `payable` function never triggers `receive()`: [4](#0-3) 

---

### Impact Explanation

Direct, unconditional loss of user ETH principal. Any excess ETH left on the router — even for one block — is claimable by an arbitrary caller. The attacker does not need any special role, pool access, or oracle manipulation. The loss equals the full surplus ETH the user sent.

---

### Likelihood Explanation

- ETH-in WETH-out swaps are a primary use case for the router.
- Users routinely over-send ETH to avoid slippage-induced reverts.
- Omitting `refundETH()` from a multicall is a common user error (no on-chain enforcement exists).
- A bot watching the mempool can detect the omission and front-run or back-run the victim's transaction with a `refundETH()` call at negligible cost.

---

### Recommendation

Bind excess ETH to the originating caller using transient storage. Record `msg.sender` at the start of each `multicall` / top-level swap entry and enforce it inside `refundETH()`:

```solidity
// pseudocode
function refundETH() external payable override {
    address entitled = _getMulticallInitiator(); // read from transient storage
    require(msg.sender == entitled, "NotEntitled");
    uint256 balance = address(this).balance;
    if (balance > 0) _transferETH(entitled, balance);
}
```

Alternatively, restrict `refundETH()` to be callable only from within an active `multicall` context (i.e., only via `delegatecall` from the same contract), so it cannot be invoked as a standalone external call.

---

### Proof of Concept

```
1. User calls router.multicall{value: 1 ether}([
       abi.encodeCall(exactInputSingle, (params))   // swap 0.5 ETH → WETH
       // refundETH() intentionally omitted
   ])
2. pay() wraps 0.5 ETH → WETH, sends to pool.
   Remaining 0.5 ETH sits at address(router).balance.
3. Attacker calls router.refundETH() in the next tx.
4. _transferETH(attacker, 0.5 ether) executes.
5. assert attacker.balance increased by 0.5 ETH; user's 0.5 ETH is gone.
```

No privileged role, malicious pool, or non-standard token is required. The only precondition is that the victim sends more ETH than the swap consumes and omits `refundETH()`.

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
