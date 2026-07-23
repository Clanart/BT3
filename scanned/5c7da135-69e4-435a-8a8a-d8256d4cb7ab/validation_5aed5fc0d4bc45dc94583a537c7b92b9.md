The `pay` function in `PeripheryPayments.sol` is the critical code to analyze. Let me verify the `receive()` guard behavior and the multicall ETH flow.

The vulnerability is real and provable from the code. Here is the full analysis:

---

### Title
Stranded ETH in Router Consumed to Fund Arbitrary User's WETH Payment, Causing Permanent Loss — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary

`PeripheryPayments.pay` unconditionally uses `address(this).balance` to partially or fully fund any WETH payment, without verifying that the ETH belongs to the current payer. ETH sent to the router via a `payable` multicall without a subsequent `refundETH` is permanently stranded and will be silently consumed to settle a later user's WETH swap.

### Finding Description

The `receive()` guard only fires when ETH arrives with empty calldata: [1](#0-0) 

When ETH is sent as `msg.value` to a `payable` function such as `multicall`, `receive()` is never invoked — the ETH is accepted directly by the function. If the user omits `refundETH` at the end of the multicall, that ETH remains in the router across transactions. [2](#0-1) 

When a subsequent user calls `exactInputSingle` (or any swap) with `tokenIn = WETH`, the callback reaches `pay`, which reads the entire contract ETH balance without any ownership check: [3](#0-2) 

The partial-payment branch (lines 78–81) wraps the stranded ETH, transfers it to the pool, and then calls `transferFrom(payer, pool, value - nativeBalance)` — pulling only the remainder from the actual payer. The stranded ETH is irrecoverably consumed.

### Impact Explanation

- **Direct, permanent loss of user principal.** The ETH owner who forgot `refundETH` loses their ETH with no recourse; it is wrapped and transferred to a pool on behalf of a different user.
- **PAYER_EXCLUSIVITY invariant broken.** The designated payer's WETH allowance is reduced by the stranded amount, meaning the swap settles using tokens from two different principals.
- Severity: **High/Critical** — direct loss of user funds, no privileged role required, no non-standard token behavior required.

### Likelihood Explanation

Sending ETH via multicall without `refundETH` is a common user mistake in Uniswap-style routers (e.g., when a prior ETH→WETH swap partially fills or the user over-sends). Any subsequent WETH swap by any user will silently drain the stranded balance. A griefing attacker can also deliberately leave dust ETH to reduce a victim's required allowance and cause their swap to fail if the victim has approved exactly the expected amount.

### Recommendation

In the WETH branch of `pay`, do not use `address(this).balance` unless `payer == address(this)` (i.e., the router itself is the source). For external payers, always pull the full amount via `transferFrom`:

```solidity
} else if (token == WETH) {
    // Only use native balance when the router itself is paying (mid-path hop).
    // For external payers, always pull via transferFrom to preserve payer exclusivity.
    IERC20(WETH).safeTransferFrom(payer, recipient, value);
}
```

If native-ETH-as-WETH-input is a desired feature, gate it explicitly (e.g., require `msg.value == amountIn` at the entry point and track it in transient storage) so only the current transaction's ETH is eligible.

### Proof of Concept

1. **Setup:** Deploy `MetricOmmSimpleRouter` with a WETH/TOKEN pool.
2. **UserB strands ETH:** UserB calls `router.multicall{value: 0.5 ether}([...])` performing some action but omitting `refundETH`. `router.balance == 0.5 ETH`.
3. **UserA swaps:** UserA approves `0.5 WETH` to the router (half of the required 1 ETH), then calls `exactInputSingle({tokenIn: WETH, amountIn: 1 ether, ...})`.
4. **`pay` executes:** `nativeBalance = 0.5 ETH`, `value = 1 ETH`. Branch at line 78 fires:
   - Wraps 0.5 ETH → 0.5 WETH, transfers to pool.
   - Calls `transferFrom(userA, pool, 0.5 WETH)` — succeeds because userA approved exactly 0.5 WETH.
5. **Result:** UserA's 1 ETH swap completes. UserB's 0.5 ETH is permanently gone. UserA effectively received a 0.5 ETH subsidy from UserB. [4](#0-3)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L69-88)
```text
  function pay(address token, address payer, address recipient, uint256 value) internal {
    // If the payer is contract it means we are in the middle of a path. In the middle of a path we operate on ERC20 only.
    if (payer == address(this)) {
      IERC20(token).safeTransfer(recipient, value);
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
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
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
