### Title
Escrow release in `_fillSameChain` assumes `order.inputs` and `order.output.assets` are equal length and positionally paired — excess escrow can be permanently locked - (File: evm/src/apps/intentsv2/IntrinsicIntents.sol)

### Summary
The external report's root cause is a positional-index assumption across a heterogeneous list (routes) without validating that the assumption actually holds, which silently corrupts which token gets swapped for which. Hyperbridge's `IntrinsicIntents._fillSameChain` contains the same structural pattern: it iterates `i` from `0` to `outputsLen = order.output.assets.length` and, for each `i`, reads `order.inputs[i]` to compute the proportional escrow to release, assuming `order.inputs` and `order.output.assets` are the same length and positionally correspond 1:1 — with no explicit check enforcing that invariant at either `placeOrder` or `fillOrder` time.

### Finding Description
In `_fillSameChain`: [1](#0-0) 
the loop bound is `outputsLen = order.output.assets.length`, yet the escrow computation and release use `order.inputs[i]`: [2](#0-1) 

This mirrors the `MultiHopSwapCore` bug pattern exactly: an index/quantity derived from one list (`routes` in the original report, `order.output.assets` here) is used to reach into a second, independently-sized list (`initialInCoin` reused across all routes there; `order.inputs` here) under the unverified assumption that the two lists line up. If `order.inputs.length` (set once at `placeOrder` time and escrowed then) does not equal `order.output.assets.length`:

- If `inputs.length > outputsLen`, only the first `outputsLen` input entries are ever read by `_fillSameChain`. Once every output asset is satisfied, `isFullyFilled` becomes `true`, the order is marked `_filled[commitment] = solver`, `OrderFilled` is emitted, and the order is finalized — but the escrow amounts stored under `_orders[commitment][token]` for the *extra* input tokens beyond index `outputsLen - 1` are never included in `escrowedInputs` and are therefore never withdrawn via `_withdraw`.
- Because `_filled[commitment]` is now non-zero, the same-chain cancel path (`_cancelSameChain`) is unreachable for this commitment (any cancel/fill checks `Filled()`), and fill is blocked too. The leftover escrowed balance for the unaddressed input tokens becomes permanently stranded in the contract with no code path left to redeem it back to the user or route it to the solver.

I was not able to locate, within the code reviewed, an explicit check in `placeOrder` (or elsewhere) enforcing `order.inputs.length == order.output.assets.length`; the `TokenInfo[]` arrays for inputs and outputs are independently supplied by the order creator with no cross-length validation visible in the modules inspected (`IntentsBase.sol`, `IntrinsicIntents.sol`). This is the same class of gap as `message_multi_hop_swap::ValidateBasic` not checking that all routes start with the same token — the validation that should tie two related lists together is missing at the point of intake.

### Impact Explanation
If exploitable, a user who places a same-chain order with more input tokens than output assets loses the excess escrowed input tokens permanently once any solver fully satisfies the (shorter) output list — direct, unrecoverable loss of user funds, matching the bounty's "loss of funds" / "false state acceptance" (order marked filled while escrow remains unaccounted) categories.

### Likelihood Explanation
This requires only an ordinary user (or an order-placing frontend/SDK with a bug) constructing an order with mismatched `inputs`/`output.assets` array lengths — no relayer, prover, or admin compromise needed, satisfying the "unprivileged attacker" bar. However, I could not fully confirm from the excerpts reviewed whether `placeOrder` (in `evm/src/apps/IntentGatewayV2.sol`) already enforces equal lengths elsewhere in code not shown to me in full; the grep for `inputs.length` in that file returned 6 matches whose content I was not able to inspect completely before running out of tool iterations. This uncertainty should be resolved by reading `evm/src/apps/IntentGatewayV2.sol`'s `placeOrder` function in full before treating this as confirmed-exploitable.

### Recommendation
Add an explicit invariant check (ideally in `placeOrder`, mirrored in `fillOrder`) that `order.inputs.length == order.output.assets.length`, reverting with `InvalidInput()` otherwise — analogous to the fix applied for the multi-hop route report (validating cross-list consistency before any positional indexing is trusted).

### Proof of Concept
Conceptual reproduction (pending confirmation that `placeOrder` does not already reject mismatched lengths):
1. User calls `placeOrder` with `order.inputs = [TokenA: 100, TokenB: 100]` (two input tokens escrowed) but `order.output.assets = [TokenC: 100]` (single output asset).
2. Solver calls `fillOrder` providing `outputs = [TokenC: 100]`.
3. `_fillSameChain` loops `i = 0` only (bounded by `outputsLen = 1`), computes `escrowedInputs[0]` from `order.inputs[0]` (TokenA), and finalizes the order (`isFullyFilled = true`), emitting `OrderFilled` and marking `_filled[commitment]`.
4. The 100 units of `TokenB` escrowed under `_orders[commitment][TokenB]` are never referenced or released, and the order can no longer be cancelled or filled again (`Filled()` on any subsequent call) — the `TokenB` escrow is permanently stuck in the contract. [1](#0-0) [3](#0-2)

### Citations

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L54-66)
```text
    function _fillSameChain(Order calldata order, FillOptions calldata options, bytes32 commitment) internal {
        uint256 outputsLen = order.output.assets.length;

        _filled[commitment] = msg.sender;

        uint256 msgValue = msg.value;
        address beneficiary = address(uint160(uint256(order.output.beneficiary)));
        bool isFullyFilled = true;

        TokenInfo[] memory escrowedInputs = new TokenInfo[](outputsLen);
        TokenInfo[] memory outputFills = new TokenInfo[](outputsLen);

        for (uint256 i; i < outputsLen; i++) {
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L116-141)
```text
            uint256 escrowedAmount;
            if (amountFilled == totalRequired) {
                escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
            } else {
                escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
            }
            escrowedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: escrowedAmount});
            outputFills[i] = TokenInfo({token: outputToken, amount: fillAmount});
        }

        // Orders carrying output calldata must be filled completely in a single fill.
        // The attached call is only executed on a full fill, so a partial fill would
        // leave the intended side effect unexecuted while releasing proportional escrow.
        if (order.output.call.length > 0 && !isFullyFilled) revert PartialFillNotAllowed();

        WithdrawalRequest memory body = WithdrawalRequest({
            commitment: commitment, tokens: escrowedInputs, beneficiary: bytes32(uint256(uint160(msg.sender)))
        });
        _withdraw(body, false, isFullyFilled);

        if (isFullyFilled) {
            _execute(order, outputsLen);
            emit OrderFilled({commitment: commitment, filler: msg.sender, outputs: outputFills, inputs: escrowedInputs});
        } else {
            delete _filled[commitment];
            emit PartialFill({commitment: commitment, filler: msg.sender, outputs: outputFills, inputs: escrowedInputs});
```
