## Analysis

The external report's core broken invariant: a per-unit "conservative" allocation (`pendingCount`) is computed without correctly accounting for the fact that it draws against a **shared pool** that other concurrent operations also depend on, so one actor's operation can consume more of the shared resource than was actually allocated to it.

The local analog lives in the same-chain intent fill path, where escrow for input tokens is tracked **per-token**, not per-output-index, but the "final fill" shortcut grabs the *entire remaining escrow balance* for a token instead of just the portion belonging to the output pair being completed.

### Title
Same-chain partial fill drains shared escrow bucket meant for a sibling unfilled output pair — ([File: evm/src/apps/intentsv2/IntrinsicIntents.sol])

### Summary
`_fillSameChain` escrows and releases input tokens keyed only by `(commitment, token address)` [1](#0-0) , not by output-pair index. `placeOrder` explicitly rejects **duplicate output tokens** via a transient-storage guard [2](#0-1) , but performs **no equivalent check on `order.inputs`**, so a user can construct an order whose two output pairs are backed by the *same* input token, merging their escrow into one bucket via `_orders[commitment][token] += ...` (accumulating escrow deposits) [3](#0-2) .

### Finding Description
In `_fillSameChain`, when a solver's fill causes one output pair to reach its `totalRequired` (`amountFilled == totalRequired`), the contract does **not** compute the proportional escrow release for that pair. Instead it takes a shortcut and releases the *entire current balance* of `_orders[commitment][token]` for that token:

```solidity
uint256 escrowedAmount;
if (amountFilled == totalRequired) {
    escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
} else {
    escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
}
``` [4](#0-3) 

This shortcut is a "minimum/maximum guarantee" heuristic exactly like the lottery's `pendingCount = 1` fallback: it substitutes a locally-conservative-looking value (full bucket balance) for the correct proportional share, without checking whether the shared bucket is also backing a *different, still-unfilled* output pair.

If a user places an order with two output pairs whose corresponding `order.inputs[i].token` are the **same address** (no duplicate-input check exists, unlike the duplicate-output-token guard at placeOrder time), the escrow for both pairs accumulates into one `_orders[commitment][token]` slot. A solver can then, in a single `fillOrder` call:
1. Fully satisfy output pair 0 (`solverAmount == totalRequired[0]`) → triggers the `amountFilled == totalRequired` branch → `escrowedAmount = _orders[commitment][token]` = **the combined escrow for both pairs**.
2. Supply `solverAmount = 0` for output pair 1 → hits the `continue` branch (line 76-79), leaving `escrowedInputs[1]` as the zero-value default, `isFullyFilled = false`.

`_withdraw` then transfers the *entire* combined escrow amount to the solver for pair-0's `TokenInfo`, decrementing `_orders[commitment][token]` to zero [5](#0-4) . Because `isFullyFilled` is `false`, `_filled[commitment]` is deleted rather than finalized [6](#0-5) , so the order is left in a state where output pair 1 was never delivered but its backing escrow is gone. A subsequent cancellation attempt by the user fails because `_orders[commitment][token]` is now `0` (`hasEscrow` is false) [7](#0-6) , so the user cannot recover the funds that were meant to be reserved for the second output.

### Impact Explanation
This lets a solver receive escrowed input tokens covering **two output obligations while only delivering one output**, extracting funds that were reserved for a sibling, still-pending leg of the same order, with no way for the order owner to reclaim them via `_cancelSameChain`. This is a direct loss/theft-of-funds and logic-attack scenario matching the bounty's accepted impact categories (unauthorized extraction of escrowed value, wrong amount released to a beneficiary).

### Likelihood Explanation
Medium: the attacker needs to be the order creator (to intentionally craft an order with two output pairs sharing one input token) colluding with, or acting as, the filling solver — both roles are unprivileged, permissionless EOAs, so no relayer/prover/admin trust is required. The missing input-deduplication check is the load-bearing gap, directly analogous to the missing pool-aware check in the original `SpinLottery` `pendingCount` logic.

### Recommendation
- Reject orders whose `order.inputs` contain duplicate token addresses at `placeOrder`, mirroring the existing duplicate-output-token guard (lines 163-179 of `IntentGatewayV2.sol`).
- Independently, in `_fillSameChain`, compute `escrowedAmount` for the "fully filled this pair" branch as `min(_orders[commitment][token], proportional share owed to this index)` rather than the raw full bucket balance, so a shared escrow slot can never be drained beyond the amount attributable to the pair being completed.

### Proof of Concept
1. User calls `placeOrder` with `order.inputs = [ {token: USDC, amount: 100}, {token: USDC, amount: 50} ]` and two distinct output pairs `outputs[0]` (e.g., 100 DAI) and `outputs[1]` (e.g., 50 DAI) — no revert occurs since only output tokens are deduped, not input tokens. Escrow accumulates: `_orders[commitment][USDC] = 150`.
2. A colluding/attacking solver calls `fillOrder` with `options.outputs[0].amount = 100` (fully satisfies pair 0) and `options.outputs[1].amount = 0`.
3. In the loop, pair 0 hits `amountFilled == totalRequired` → `escrowedAmount = _orders[commitment][USDC] = 150` is placed into `escrowedInputs[0]`. Pair 1 hits the `solverAmount == 0` branch and is skipped, `isFullyFilled = false`.
4. `_withdraw` sends the solver **150 USDC** (both pairs' escrow) while the solver only delivered 100 DAI for pair 0.
5. `_filled[commitment]` is deleted (order left "not fully filled"), but `_orders[commitment][USDC] == 0`, so `_cancelSameChain` reverts with `UnknownOrder()` — the user cannot recover the 50 USDC that was meant to back the still-unfulfilled output pair 1.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L140-140)
```text
    mapping(bytes32 => mapping(address => uint256)) public _orders;
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L400-409)
```text
            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L163-179)
```text
        if (order.inputs.length == 0) revert InvalidInput();

        // Reject duplicate output tokens 
        uint256 outputsLen_ = order.output.assets.length;
        for (uint256 i; i < outputsLen_;) {
            bytes32 token = order.output.assets[i].token;
            assembly ("memory-safe") {
                if tload(token) {
                    mstore(0, 0xb4fa3fb3) // InvalidInput.selector
                    revert(0x1c, 0x04)
                }
                tstore(token, 1)
            }
            unchecked {
                ++i;
            }
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L434-435)
```text
                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L116-122)
```text
            uint256 escrowedAmount;
            if (amountFilled == totalRequired) {
                escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
            } else {
                escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
            }
            escrowedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: escrowedAmount});
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L139-141)
```text
        } else {
            delete _filled[commitment];
            emit PartialFill({commitment: commitment, filler: msg.sender, outputs: outputFills, inputs: escrowedInputs});
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L172-181)
```text
        for (uint256 i; i < inputsLen;) {
            address token = address(uint160(uint256(order.inputs[i].token)));
            uint256 escrowed = _orders[commitment][token];
            if (escrowed > 0) hasEscrow = true;
            remainingTokens[i] = TokenInfo({token: order.inputs[i].token, amount: escrowed});
            unchecked {
                ++i;
            }
        }
        if (!hasEscrow) revert UnknownOrder();
```
