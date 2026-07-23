The code is clear. Let me confirm the exact `refundETH` implementation and the `pay()` ETH consumption logic before rendering a verdict.

The code is fully clear. Here is the verdict:

---

### Title
Public `refundETH()` with no caller binding allows any address to steal excess ETH stranded on the router by a prior user — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`refundETH()` is an unrestricted public function that transfers the router's entire native ETH balance to `msg.sender`. Because `pay()` only wraps the exact swap amount (not the full `msg.value`), any excess ETH sent with a `multicall{value}` call remains on the router after the swap completes. If the user omits `refundETH` from their multicall, that ETH is claimable by any subsequent caller in a separate transaction.

---

### Finding Description

`refundETH()` contains zero access control:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);   // sends to whoever calls
    }
}
``` [1](#0-0) 

When `pay()` handles a WETH-input swap it deposits exactly `value` (the swap amount), leaving any surplus native ETH on the contract:

```solidity
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();   // only `value`, not full balance
    IERC20(WETH).safeTransfer(recipient, value);
}
``` [2](#0-1) 

The `receive()` guard only blocks *direct* ETH pushes from non-WETH addresses; it does not prevent ETH from accumulating via `payable` entry points (`multicall`, `exactInputSingle`, `exactOutputSingle`, etc.). [3](#0-2) 

`multicall` is `payable` and simply `delegatecall`s each encoded call in sequence with no post-call ETH accounting: [4](#0-3) 

If the user's multicall does not include a `refundETH` step, the excess ETH persists on the router across transaction boundaries and is claimable by any address.

---

### Impact Explanation

Direct loss of user principal. Any ETH overpayment left on the router after a completed multicall is immediately claimable by an arbitrary caller. An MEV bot monitoring the mempool can front-run or back-run the victim's transaction and drain the full stranded balance in one call. No privileged role, malicious pool, or non-standard token is required.

---

### Likelihood Explanation

The intended usage pattern (documented in `IMetricOmmPoolLiquidityAdder` and demonstrated in tests) is to include `refundETH` as the last step of every ETH-input multicall. Users interacting directly with the contract, using third-party frontends, or constructing multicalls programmatically can easily omit this step. MEV infrastructure makes the theft essentially guaranteed once the victim's transaction is confirmed without the refund step. [5](#0-4) 

---

### Recommendation

Restrict `refundETH` to only be callable within a multicall context (i.e., via `delegatecall` from `multicall`), or record the originating `msg.sender` at multicall entry in transient storage and enforce that `refundETH` only transfers to that stored address. The simplest safe fix is to check that the call is a `delegatecall` (compare `address(this)` to the implementation address) so `refundETH` cannot be invoked as a standalone external call.

---

### Proof of Concept

1. User calls `router.multicall{value: 1 ether}([exactInputSingle(weth→token1, amountIn=0.5 ETH, ...)])` — no `refundETH` step included.
2. Inside the swap callback, `pay()` deposits exactly `0.5 ETH` as WETH and transfers it to the pool. The remaining `0.5 ETH` stays on the router.
3. Multicall returns successfully. Router now holds `0.5 ETH`.
4. Attacker calls `router.refundETH()` in a separate transaction.
5. `refundETH` reads `address(this).balance == 0.5 ETH` and calls `_transferETH(msg.sender, 0.5 ETH)`.
6. Attacker receives `0.5 ETH`; victim's overpayment is permanently lost.

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L75-77)
```text
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

**File:** metric-periphery/contracts/interfaces/IMetricOmmPoolLiquidityAdder.sol (L15-17)
```text
/// @dev Native ETH input uses the same multicall pattern as the swap router: send ETH with the add call (or
///      `multicall{value}`) when the pool's WETH leg is token0 or token1; unused ETH can be reclaimed via
///      `refundETH` in the same multicall.
```
