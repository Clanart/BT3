## Title
Solver can drain an order's entire escrowed balance for a duplicated input token by exploiting positional input/output pairing in `_fillSameChain()` - (`evm/src/apps/intentsv2/IntrinsicIntents.sol`)

### Summary
`IntentGatewayV2.placeOrder()` deduplicates `order.output.assets` tokens but never checks `order.inputs` for duplicate token entries [1](#0-0) . `_fillSameChain()` then pairs `order.output.assets[i]` with `order.inputs[i]` purely by array index, and when an output slot is fully completed it reads the *entire current* `_orders[commitment][token]` balance for that token rather than the amount specifically escrowed at index `i` [2](#0-1) . If a token repeats across `order.inputs`, a solver can fully satisfy the cheapest output slot that maps to that repeated token while leaving all other outputs unfilled (by supplying `solverAmount = 0` for them), and receive the token's *full* escrowed balance instead of the proportional share tied to that output.

### Finding Description
`placeOrder()` only guards against duplicate **output** tokens using transient storage (`tload`/`tstore`) [3](#0-2) . There is no equivalent uniqueness check for `order.inputs`; each input entry is transferred into escrow and accumulated into the same mapping slot `_orders[commitment][token]` regardless of how many array entries reference the same token [4](#0-3) .

`_fillSameChain()` iterates output assets by index `i` and, for the same index, unconditionally reads `order.inputs[i]` as "the input tied to this output" [5](#0-4) . Crucially, when a given output slot reaches full completion (`amountFilled == totalRequired`), the code does **not** release the proportional amount recorded for that index; it instead releases whatever is currently sitting in `_orders[commitment][token]` for `order.inputs[i].token`:

```solidity
if (amountFilled == totalRequired) {
    escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
} else {
    escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
}
escrowedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: escrowedAmount});
``` [2](#0-1) 

Under a normal order (unique input tokens, 1:1 with outputs) this is harmless because the mapping only ever holds that one input's amount for that token. But if `order.inputs` contains the same token twice at different indices (e.g. a cheap slice and an expensive slice), the mapping `_orders[commitment][token]` holds the *sum* of both. Fully completing only the output slot associated with the cheap input slice causes `_orders[commitment][token]` (the combined balance of both slices) to be read and transferred in full via `_withdraw()`, which simply decrements and pays out whatever amount is passed in the `WithdrawalRequest` without ever checking it against the specific input index's intended share [6](#0-5) [7](#0-6) .

Because `remaining == 0 || solverAmount == 0` lets a caller skip any output index in a given `fillOrder` call (`continue`, leaving that `escrowedInputs[i]` as a zero-value entry) [8](#0-7) , a solver can complete only the cheap output while leaving the expensive one at `solverAmount = 0`, and still drain the full combined escrow tied to the shared token.

### Impact Explanation
This is a direct fund-loss / transaction-manipulation vector: an unprivileged solver, an ordinary market participant with no elevated permissions, can pay only for the cheapest output leg of an order and receive the *entire* escrowed balance of a token that is split across two (or more) `order.inputs` entries. The remaining output leg becomes permanently unfulfillable once the shared token's escrow hits zero, because any later completion attempt hits `if (escrowed == 0) revert UnknownOrder();` in `_withdraw()` [9](#0-8) , locking the order in an unresolvable partially-filled state. This mirrors exactly the external report's broken invariant — the absence of a uniqueness/duplication check within one side of an order lets an actor receive value disproportionate to what they provided, at the counterparty's expense.

### Likelihood Explanation
The only precondition is that a placed order's `inputs` array contains the same token at more than one index — nothing in `placeOrder()` rejects this, and nothing in the SDK/UI is enforced on-chain. Any user order structured this way (intentionally or not) is exploitable by any competing solver simply by choosing which output amounts to fill in a normal `fillOrder()` call; no relayer, prover, governance, or malicious-peer assumption is required.

### Recommendation
- In `placeOrder()`, add the same duplicate-token rejection currently applied to `order.output.assets` to `order.inputs`, so each input token can only appear once per order.
- In `_fillSameChain()`, stop reading the raw `_orders[commitment][token]` mapping balance on full completion; instead track and release only the amount specifically attributable to `order.inputs[i]` (e.g., via a per-index escrowed-amount ledger rather than a per-token aggregate), so a completed output slot can never release more than its own paired input's remaining balance.

### Proof of Concept
1. User calls `placeOrder()` with:
   - `inputs = [{token: tokenA, amount: 10}, {token: tokenA, amount: 990}]` (same token, two slices, totaling 1000 escrowed in `_orders[commitment][tokenA]`).
   - `output.assets = [{token: tokenX, amount: 1}, {token: tokenY, amount: 1000}]`.
   - `placeOrder()` accepts this order unmodified since only `output.assets` duplicates are checked [3](#0-2) .
2. A solver calls `fillOrder(order, {outputs: [{tokenX, 1}, {tokenY, 0}]})`.
   - For `i = 0`: `totalRequired = 1`, `solverAmount = 1` → `amountFilled == totalRequired` → `escrowedAmount = _orders[commitment][tokenA] = 1000` (the full combined balance) [10](#0-9) .
   - For `i = 1`: `solverAmount = 0`, `remaining = 1000 > 0` → `isFullyFilled = false; continue;` (output Y untouched).
   - `_withdraw()` is called with `tokens = [{tokenA, 1000}, {tokenA, 0}]`; the solver receives the full 1000 `tokenA` for having supplied only 1 unit of `tokenX` [11](#0-10) .
   - `isFullyFilled == false` deletes `_filled[commitment]`, allowing (fruitless) future fill attempts, but `_orders[commitment][tokenA]` is now `0`, so output Y can never be completed — the user has lost all of `tokenA` and will never receive the promised `tokenY` payment.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L163-189)
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

**File:** evm/src/apps/IntentGatewayV2.sol (L282-298)
```text
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                if (token == address(0)) {
                    if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
                    msgValue -= order.inputs[i].amount;
                } else {
                    uint256 balBefore = IERC20(token).balanceOf(address(this));
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                    order.inputs[i].amount = IERC20(token).balanceOf(address(this)) - balBefore;
                }

                unchecked {
                    ++i;
                }
            }
        }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L66-123)
```text
        for (uint256 i; i < outputsLen; i++) {
            bytes32 outputToken = order.output.assets[i].token;
            if (options.outputs[i].token != outputToken) revert InvalidInput();

            address token = address(uint160(uint256(outputToken)));
            uint256 totalRequired = order.output.assets[i].amount;
            uint256 solverAmount = options.outputs[i].amount;

            uint256 alreadyFilled = _partialFills[commitment][outputToken];
            uint256 remaining = totalRequired - alreadyFilled;
            if (remaining == 0 || solverAmount == 0) {
                if (solverAmount == 0 && remaining > 0) isFullyFilled = false;
                continue;
            }
            uint256 fillAmount;

            uint256 beneficiaryShare = 0;
            uint256 protocolShare = 0;
            if (alreadyFilled == 0 && solverAmount > totalRequired) {
                fillAmount = totalRequired;
                uint256 dust = solverAmount - totalRequired;
                if (order.output.call.length > 0) {
                    protocolShare = dust;
                } else {
                    protocolShare = (dust * _params.surplusShareBps) / 10_000;
                    beneficiaryShare = dust - protocolShare;
                }
            } else {
                fillAmount = solverAmount > remaining ? remaining : solverAmount;
            }

            uint256 amountFilled = alreadyFilled + fillAmount;
            _partialFills[commitment][outputToken] = amountFilled;
            uint256 beneficiaryTotal = fillAmount + beneficiaryShare;

            if (token == address(0)) {
                if (msgValue < beneficiaryTotal + protocolShare) revert InsufficientNativeToken();
                msgValue -= (beneficiaryTotal + protocolShare);
                (bool sent,) = beneficiary.call{value: beneficiaryTotal}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransferFrom(msg.sender, beneficiary, beneficiaryTotal);
                if (protocolShare > 0) {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), protocolShare);
                }
            }

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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-410)
```text
    function _withdraw(WithdrawalRequest memory body, bool isRefund, bool finalize) internal {
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
