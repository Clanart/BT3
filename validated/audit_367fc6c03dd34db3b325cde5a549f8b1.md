## Title
Duplicate input tokens across order legs let a solver double-claim escrowed funds via `_fillSameChain()`'s per-token escrow lookup - (File: `evm/src/apps/intentsv2/IntrinsicIntents.sol`)

### Summary
`IntentGatewayV2.placeOrder()` only rejects duplicate **output** tokens, not duplicate **input** tokens. Because escrow is tracked in a `mapping(commitment => mapping(token => amount))` (`_orders`) keyed only by token address, two output legs that reference the same input token share one escrow bucket. In `IntrinsicIntents._fillSameChain()`, when a leg becomes fully filled the code releases `_orders[commitment][token]` (the *entire current balance* for that token) instead of that leg's proportional share — the same class of bug as the reported `TradingUtils._executeTrade()` issue, where a balance snapshot/lookup is not correctly scoped to the specific leg/trade being settled, so an unrelated portion of the balance gets swept along with it.

### Finding Description
In `evm/src/apps/IntentGatewayV2.sol::placeOrder()`, only `order.output.assets[i].token` duplicates are rejected via transient storage: [1](#0-0) 
`order.inputs` has no equivalent uniqueness check, so a user can freely create an order with two (or more) `inputs[i].token` entries pointing at the same ERC20/native token address, each escrowed under the same `_orders[commitment][token]` bucket.

In `IntrinsicIntents._fillSameChain()`, when a leg's cumulative fill reaches `totalRequired`, the escrow amount released for that leg is read directly from the shared bucket rather than computed from that leg's own share: [2](#0-1) 

Because two legs (`i=0`, `i=1`) can point at the same `order.inputs[i].token`, fully filling leg 0 alone causes `escrowedAmount = _orders[commitment][token]` to equal the **combined** escrow for both legs (`A0 + A1`), not just leg 0's `A0`. This value flows into `_withdraw()`: [3](#0-2) 
which sets `_orders[commitment][token] = 0` and pays the **whole** amount to `msg.sender` (the solver filling leg 0), even though leg 1's output requirement was never met. `_partialFills` is tracked per output token, so leg 1 remains outstanding and can be filled later by a different, honest solver — who delivers real output tokens to the beneficiary but, because `_orders[commitment][token]` is now `0`, receives `escrowedAmount = 0` back (silently skipped by `if (amount == 0) continue;` in `_withdraw`, so the call does not even revert).

This is directly analogous to the `TradingUtils._executeTrade()` bug: a balance value (`preTradeBalance` there, `_orders[commitment][token]` here) is read without being scoped to the specific leg/trade currently being processed, so it silently absorbs value belonging to an unrelated leg, and that unrelated value is transferred out to the wrong party.

### Impact Explanation
A malicious order creator can craft an order with duplicate input tokens split across two output legs, then (using a second address, or colluding with an accomplice) fill one leg first to drain the *entire* escrow for that token in one payout. Any subsequent, honest solver who fills the remaining leg delivers real output value to the beneficiary but receives zero escrowed input tokens in return — a direct loss of funds for that solver, and the order still finalizes as `OrderFilled`. This is fund theft/loss reachable by any unprivileged user/solver pair with no relayer, prover, or admin compromise required, matching the bounty's "stealing or loss of funds" / "logic attacks" / "double-claim" categories.

### Likelihood Explanation
High — the only precondition is that `placeOrder()` accepts an order with duplicate `inputs[i].token` values, which is not blocked anywhere in the codebase (only output-token duplicates are checked). Constructing such an order and executing a two-step fill (fill leg 0 fully first, leave leg 1 pending) requires only normal, permissionless calls to `placeOrder()` and `fillOrder()`.

### Recommendation
- Reject duplicate `order.inputs[i].token` values in `placeOrder()`, mirroring the existing output-token duplicate check, or
- Track escrow per `(commitment, inputIndex)` instead of `(commitment, token)`, so each leg's release is bound to its own contribution and cannot draw down another leg's escrow, and
- In `_fillSameChain()`, compute `escrowedAmount` from the leg's own recorded input amount (proportional to `order.inputs[i].amount`) rather than reading the shared `_orders[commitment][token]` balance directly.

### Proof of Concept
1. Attacker calls `placeOrder()` with:
   - `inputs = [{token: T, amount: A0}, {token: T, amount: A1}]` (same token `T` twice)
   - `output.assets = [{token: O0, amount: R0}, {token: O1, amount: R1}]`
   - Escrow recorded as `_orders[commitment][T] = A0 + A1`.
2. Attacker (as solver) calls `fillOrder()` supplying `outputs = [{O0, R0}, {O1, 0}]`.
   - Leg 0: `amountFilled == totalRequired` → `escrowedAmount = _orders[commitment][T] = A0 + A1` [4](#0-3) .
   - Leg 1: `solverAmount == 0` → skipped, `isFullyFilled = false`.
   - `_withdraw()` pays `A0 + A1` to the attacker (`msg.sender`) and zeroes `_orders[commitment][T]` [5](#0-4) .
   - Order is marked as a `PartialFill`, leg 1 (`O1`/`R1`) still outstanding.
3. An honest solver later calls `fillOrder()` supplying `outputs = [{O0, 0}, {O1, R1}]`, delivering real `O1` tokens to the beneficiary. Leg 1 now reaches `totalRequired`, so `escrowedAmount = _orders[commitment][T] = 0`; `_withdraw()` skips payout via `if (amount == 0) continue;`, and the order finalizes as fully filled with the honest solver receiving nothing for their `R1` delivery.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L165-189)
```text
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
        // Clean up transient storage so repeated placeOrder calls in the same tx don't false-positive.
        for (uint256 i; i < outputsLen_;) {
            bytes32 token = order.output.assets[i].token;
            assembly ("memory-safe") {
                tstore(token, 0)
            }
            unchecked {
                ++i;
            }
        }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L113-123)
```text
            if (totalRequired > amountFilled) isFullyFilled = false;
            if (protocolShare > 0) emit DustCollected(token, protocolShare);

            uint256 escrowedAmount;
            if (amountFilled == totalRequired) {
                escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
            } else {
                escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
            }
            escrowedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: escrowedAmount});
            outputFills[i] = TokenInfo({token: outputToken, amount: fillAmount});
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L391-410)
```text
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        if (finalize) _filled[body.commitment] = beneficiary;

        uint256 len = body.tokens.length;
        for (uint256 i; i < len; i++) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (amount == 0) continue;

            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
        }
```
