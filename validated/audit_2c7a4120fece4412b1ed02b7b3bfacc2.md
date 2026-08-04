Confirmed: `_withdraw` has no length-consistency check against `order.inputs`, and `placeOrder` never enforces `order.inputs.length == order.output.assets.length`. The bug is real and locally provable.

### Title
Same-chain fill escrow release uses output-array index against the input-token array, permanently locking un-indexed escrow when `order.inputs.length > order.output.assets.length` - (File: `evm/src/apps/intentsv2/IntrinsicIntents.sol`)

### Summary
`IntrinsicIntents._fillSameChain` computes the escrow amount to release to the solver by indexing `order.inputs[i]`, where `i` is the loop counter over `order.output.assets` (`outputsLen`), not over `order.inputs`. This silently assumes a 1:1 positional correspondence between input tokens and output tokens that is never validated anywhere in `placeOrder` or `fillOrder`. When a user escrows more input tokens than the number of distinct output assets requested (a fully valid, common pattern — e.g. escrowing USDC + WETH for a single DAI output), the fill path only ever builds a release list of length `outputsLen`, so any input token at index `>= outputsLen` is never included in the `WithdrawalRequest.tokens` array passed to `_withdraw`.

### Finding Description
In `_fillSameChain` (`evm/src/apps/intentsv2/IntrinsicIntents.sol:54-149`):
- `_filled[commitment] = msg.sender;` is set unconditionally at line 57, **before** any escrow accounting happens.
- The loop bound is `outputsLen = order.output.assets.length` (line 55, 66).
- Escrow release amounts are read using `order.inputs[i]` at lines 118, 120, 122, where `i` is the output-side loop index: [1](#0-0) 
- `escrowedInputs` is sized `outputsLen` (line 63), so only the first `outputsLen` entries of `order.inputs` are ever considered for release.
- `_withdraw` (`evm/src/apps/intentsv2/IntentsBase.sol:390-425`) only decrements `_orders[commitment][token]` for tokens present in `body.tokens` — it has no awareness of, or reconciliation against, the full `order.inputs` array. [2](#0-1) 
- Nothing in `placeOrder` (`evm/src/apps/IntentGatewayV2.sol:301-360` / `evm/tron/contracts/apps/IntentGatewayV2.sol:332-390`) enforces `order.inputs.length == order.output.assets.length`; it only checks `order.inputs.length == 0`. [3](#0-2) 

Consequence: if `order.inputs.length > order.output.assets.length`, on a full fill the order is marked `_filled[commitment] = solver` (finalized), but the trailing input tokens (`order.inputs[outputsLen..inputsLen-1]`) never appear in `escrowedInputs`, so their balances in `_orders[commitment][token]` are never decremented or transferred anywhere. Because the order is now "Filled", `cancelOrder` — which is the only other path that reads the full `order.inputs` array and refunds via `_cancelSameChain`/`_withdraw` — is permanently blocked: `IntentGatewayV2.cancelOrder` reverts with `Filled()` once `_filled[commitment] != address(0)`. [4](#0-3) 

This is the same broken-invariant class as the external report: a per-item share/amount is computed and consumed without being conditioned on the actual cardinality/structure of the collection it's drawn from (there, collector shares summed without dividing by `tokenIds.length`; here, input-token release indexed by the wrong array's length), producing state loss instead of the `swap()` report's underflow-revert.

### Impact Explanation
This is a genuine, unauthorized loss/lock of user funds in the production `IntentGatewayV2` same-chain settlement path — not a griefing-only self-inflicted scenario, since a user placing a perfectly legitimate multi-input, single-output order (a normal use case, e.g. escrowing two tokens to buy one) has no way to know the contract will strand the extra input token. The escrow becomes permanently unrecoverable: no `cancelOrder` (blocked by `Filled()`), no further `fillOrder` (also blocked by `Filled()`), and the tokens sit in `_orders[commitment][token]` with no code path left to reference that `(commitment, token)` pair. Governance's `_sweepDust`/`SweepDust` path only sweeps protocol-owned dust balances passed explicitly by governance, not user escrow keyed by commitment, so it isn't a remediation path for this specific mapping entry either.

### Likelihood Explanation
High likelihood of accidental triggering: this requires no attacker, relayer, or malicious peer — just an ordinary user (or an SDK bug) constructing an `Order` with `inputs.length != output.assets.length`, which is a structurally valid and plausible request shape (multi-asset-in, single-asset-out swaps). No validation anywhere in `placeOrder` rejects this shape.

### Recommendation
Enforce `order.inputs.length == order.output.assets.length` at `placeOrder` time (reverting with `InvalidInput()` otherwise) if the fill logic is to keep its 1:1 positional assumption; or, more robustly, decouple escrow release from the output loop index entirely — iterate over `order.inputs` directly by token address using `_orders[commitment][token]` lookups (as `_cancelSameChain` already correctly does), rather than assuming any correspondence with `order.output.assets` indices.

### Proof of Concept
1. User calls `placeOrder` with `order.inputs = [USDC: 1000, WETH: 1 ether]` and `order.output.assets = [DAI: 900]` (2 inputs, 1 output — no revert, since only `inputs.length == 0` is checked).
2. `_orders[commitment][USDC] = 1000`, `_orders[commitment][WETH] = 1 ether` are both escrowed.
3. Solver calls `fillOrder` providing `900 DAI`. In `_fillSameChain`, `outputsLen = 1`, so the loop runs only `i = 0`, building `escrowedInputs = [ {token: USDC, amount: 1000} ]` from `order.inputs[0]`. `order.inputs[1]` (WETH) is never referenced.
4. `_filled[commitment] = solver` is finalized (`isFullyFilled = true`), `_withdraw` releases only the 1000 USDC to the solver.
5. `_orders[commitment][WETH]` still holds `1 ether`, but `cancelOrder(order, ...)` now reverts with `Filled()` since `_filled[commitment] != address(0)`, and no other function references `_orders[commitment][WETH]` again. The escrowed WETH is permanently stranded in the contract.

### Citations

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L116-123)
```text
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

**File:** evm/src/apps/IntentGatewayV2.sol (L309-331)
```text
        if (protocolFeeBps > 0) {
            reducedInputs = new TokenInfo[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                uint256 originalAmount = order.inputs[i].amount;
                if (originalAmount == 0) revert InvalidInput();
                uint256 protocolFee = (originalAmount * protocolFeeBps) / 10_000;
                uint256 reducedAmount = originalAmount - protocolFee;
                address token = address(uint160(uint256(order.inputs[i].token)));

                if (protocolFee > 0) emit DustCollected(token, protocolFee);

                reducedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: reducedAmount});
                unchecked {
                    ++i;
                }
            }

            order.inputs = reducedInputs;
            commitment = keccak256(abi.encode(order));
        } else {
            reducedInputs = order.inputs;
            commitment = keccak256(abi.encode(order));
        }
```

**File:** docs/content/developers/evm/intent-gateway/cancelling-orders.mdx (L42-45)
```text
function cancelOrder(Order calldata order, CancelOptions calldata options) public payable nonReentrant {
    bytes32 commitment = keccak256(abi.encode(order));
    if (_filled[commitment] != address(0)) revert Filled();

```
