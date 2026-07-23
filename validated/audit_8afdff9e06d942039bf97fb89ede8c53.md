Audit Report

## Title
Stranded ETH from Prior User Consumed by Attacker's WETH Swap via `PeripheryPayments.pay()` Partial-ETH Branch — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.pay()` contains a partial-ETH hybrid branch that uses the router's entire `address(this).balance` as a subsidy when paying for a WETH swap, with no per-user ETH accounting. ETH left on the router by a prior user who omitted `refundETH()` is silently consumed to cover part of a subsequent caller's swap input, causing direct, irrecoverable loss of the prior user's ETH principal.

## Finding Description
The `pay()` function at lines 73–84 of `PeripheryPayments.sol` has three branches for `token == WETH`:

- **Full-ETH branch** (L75–77): `nativeBalance >= value` → deposit exactly `value` ETH as WETH and transfer to recipient.
- **Partial-ETH branch** (L78–81): `0 < nativeBalance < value` → deposit **all** `nativeBalance` as WETH, transfer it to recipient, then pull only `value - nativeBalance` from `payer` via `safeTransferFrom`.
- **No-ETH branch** (L82–83): `nativeBalance == 0` → pull full `value` from `payer`.

The partial-ETH branch treats the entire router ETH balance as freely available subsidy for the current caller, with no mechanism to distinguish which user deposited which ETH.

ETH can be stranded on the router because `exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, and `multicall` are all `payable`. The `receive()` guard at L32–34 only blocks direct ETH transfers from non-WETH addresses; it does **not** prevent ETH from arriving via a `payable` function call. A user who sends excess ETH (e.g., `exactInputSingle{value: 2 ETH}` with `amountIn=1 ETH`) and omits the separate `refundETH()` call leaves that ETH permanently accessible to the next caller.

**Exploit path:**
1. User A calls `exactInputSingle{value: 2 ETH}` with `tokenIn=WETH`, `amountIn=1 ETH`. The swap consumes 1 ETH; 1 ETH remains on the router because A did not call `refundETH()`.
2. Attacker B calls `exactInputSingle` (no ETH sent) with `tokenIn=WETH`, `amountIn=2 ETH`, having approved only 1 WETH.
3. The pool callback invokes `_justPayCallback` → `pay(WETH, B, pool, 2e18)`.
4. `nativeBalance = 1 ETH` → partial branch fires: router deposits A's 1 ETH as WETH and sends it to the pool, then pulls only 1 WETH from B.
5. A's 1 ETH is irrecoverably consumed; B's effective cost is halved. [1](#0-0) [2](#0-1) [3](#0-2) 

## Impact Explanation
Direct loss of prior user's ETH principal. The lost amount equals `min(stranded ETH, amountIn)` and can be arbitrarily large. The attacker receives a proportional discount on their swap input at the victim's expense. This is a Critical/High direct loss of user principal with no recovery path, satisfying Sherlock contest thresholds. [4](#0-3) 

## Likelihood Explanation
`refundETH()` is a separate, optional call; users who wrap ETH swaps in a single `multicall` without appending `refundETH()` will routinely leave dust or full excess ETH on the router. The router's ETH balance is publicly readable on-chain, so an attacker can monitor it and trigger the exploit in the very next block. No special role, malicious pool, or non-standard token is required — only a legitimate WETH swap through a factory-registered pool. [5](#0-4) [6](#0-5) 

## Recommendation
Remove the partial-ETH hybrid branch entirely. The router should either:
1. **Use only native ETH** when `msg.value >= value`: deposit exactly `value` and refund the rest atomically within the same call, or
2. **Use only `safeTransferFrom`**: pull WETH from the payer directly, ignoring `address(this).balance`.

Additionally, consider tracking per-call ETH budgets in transient storage so that `pay()` can only spend ETH that arrived in the current top-level call, preventing cross-user ETH leakage entirely. [7](#0-6) 

## Proof of Concept
```solidity
function test_strandedEthConsumedByAttacker() public {
    // 1. User A swaps WETH->tokenOut, sends 2 ETH, amountIn = 1 ETH
    //    A forgets refundETH(); 1 ETH remains on router.
    vm.prank(userA);
    router.exactInputSingle{value: 2 ether}(ExactInputSingleParams({
        tokenIn: WETH, amountIn: 1 ether, ...
    }));
    assertEq(address(router).balance, 1 ether); // A's leftover

    // 2. Attacker B approves only 1 WETH (not 2), calls exactInputSingle amountIn=2 ETH
    deal(WETH, attacker, 1 ether);
    IERC20(WETH).approve(address(router), 1 ether);
    vm.prank(attacker);
    router.exactInputSingle(ExactInputSingleParams({
        tokenIn: WETH, amountIn: 2 ether, ...
    }));

    // 3. Assert: router ETH balance is 0 (A's ETH consumed),
    //    B's WETH balance is 0 (only 1 WETH pulled from B),
    //    pool received full 2 WETH worth of input.
    assertEq(address(router).balance, 0);
    assertEq(IERC20(WETH).balanceOf(attacker), 0);
    // A's 1 ETH is gone; B paid only 1 WETH instead of 2.
}
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L57-63)
```text
  /// @inheritdoc IPeripheryPayments
  function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-88)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
```
