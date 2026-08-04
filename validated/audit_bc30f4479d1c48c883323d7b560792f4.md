### Title
Missing `inputs.length == output.assets.length` validation lets a filler drain disproportionate escrow via positional index mismatch - ([File: evm/src/apps/intentsv2/IntrinsicIntents.sol])

### Summary
`IntentGatewayV2.placeOrder` never validates that `order.inputs.length` matches `order.output.assets.length`, yet `_fillSameChain` in `IntrinsicIntents.sol` releases escrow by blindly indexing `order.inputs[i]` with the loop counter `i` that iterates over `order.output.assets` (`outputsLen`). This is the same root-cause class as the reported `StarBaseDCA` bug: a value that determines a payout (`curTakerFillAmount` there, `escrowedAmount` here) is trusted without validating the structural precondition that ties it to what was actually paid, letting a counterparty extract more value than they provided.

### Finding Description
`placeOrder` only checks: [1](#0-0) 

There is no assertion that `order.inputs.length == order.output.assets.length`, and no per-index binding recorded between a specific input token and a specific output token beyond positional order.

`_fillSameChain` then walks `outputsLen = order.output.assets.length` and, for every output index `i`, reads `order.inputs[i]` to decide how much escrow to release for a completed leg: [2](#0-1) 

and on full completion of that output leg releases the entire current escrow balance keyed by `order.inputs[i].token`: [3](#0-2) 

If `order.inputs.length > order.output.assets.length` (nothing rejects this at `placeOrder`), the extra input tokens beyond `outputsLen` are escrowed under `_orders[commitment][token]` at placement time but are never addressed by the fill loop's index space — so any output leg whose index `i` collides with a *different, larger* input token (e.g., `order.inputs[0]` is a large-value token but `order.output.assets[0]` is a cheap/low-value output) causes `escrowedAmount = _orders[commitment][order.inputs[0].token]` to release the **full** large-value escrow to a filler who only paid the small output amount at index 0. The remaining, unrelated input tokens at higher indices become permanently stuck (never referenced by any output index), while the filler has already been paid `escrowedAmount` for an unrelated, cheap fill.

This mirrors the reported bug's mechanics exactly: the contract trusts a solver/filler-facing value (`fillAmount`/`curTakerFillAmount` equivalent) to gate a payout (`escrowedAmount`/`order.inAmount`) without validating the structural correspondence (`order.minOutAmountPerCycle` equivalent — here, "index i input must actually correspond to index i output") that the payout size depends on.

### Impact Explanation
An order creator (or a malformed/buggy integrator building orders programmatically) that constructs an order with `inputs.length != output.assets.length` exposes escrowed funds to being drained by any filler for a fraction of their true value: the filler pays for the cheapest output leg and collects the escrow tied to a mismatched, more valuable input index. This is a direct "unauthorized/wrong-amount fund release" — the exact bounty category of stealing funds via an unvalidated value driving a token transfer.

### Likelihood Explanation
Exploitability requires only that an order with mismatched `inputs`/`outputs` lengths exists and gets filled — no privileged role, relayer, or prover is needed; any address can call `fillOrder`/trigger `_fillSameChain` as a filler. The likelihood hinges on such orders being placeable, which the code currently permits since `placeOrder` performs no length parity check between `order.inputs` and `order.output.assets`.

### Recommendation
In `IntentGatewayV2.placeOrder`, require `order.inputs.length == order.output.assets.length` (or otherwise explicitly bind each input to a specific output rather than relying on positional array indices), and add a defensive assertion in `_fillSameChain` that reverts if the arrays are not the expected length before indexing `order.inputs[i]`.

### Proof of Concept
1. User calls `placeOrder` with `order.inputs = [{tokenA, 1_000_000}, {tokenB, 1}]` and `order.output.assets = [{tokenX, 1}]` (only one output leg; two inputs escrowed). No revert occurs today because no length-parity check exists.
2. A filler calls `fillOrder`/`_fillSameChain` with `options.outputs[0] = {tokenX, 1}`, paying `1` unit of `tokenX` to the beneficiary.
3. Since `amountFilled == totalRequired` for output index 0, `escrowedAmount = _orders[commitment][tokenA]` (the full 1,000,000 `tokenA` escrow) is released to the filler via `_withdraw`, even though the filler only paid 1 unit of `tokenX`.
4. `tokenB`'s escrow (never referenced by any output index) remains permanently locked in the contract. [4](#0-3) [5](#0-4)

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L162-196)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable nonReentrant {
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

        address hostAddr = host();
        order.user = bytes32(uint256(uint160(msg.sender)));
        order.source = IDispatcher(hostAddr).host();
        order.nonce = _nonce++;

        uint256 inputsLen = order.inputs.length;
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L66-79)
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
