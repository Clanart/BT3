Based on the code I was able to examine, the strongest local analog to the "unchecked zero amount" bug class is in `IntrinsicIntents._fillSameChain()`.

### Title
Same-chain order fill accepts zero-amount solver output, corrupting the fill-attribution state before any value is transferred - (File: evm/src/apps/intentsv2/IntrinsicIntents.sol)

### Summary
`_fillSameChain()` unconditionally writes `_filled[commitment] = msg.sender` at function entry, before validating that the caller supplied any non-zero output amount. The per-output loop then treats `solverAmount == 0` as a legitimate no-op ("continue"), meaning a caller can invoke the fill path with all `options.outputs[i].amount` set to zero, pay nothing to the beneficiary, and still have the commitment's fill-attribution slot claimed under their address. [1](#0-0) 

### Finding Description
`_fillSameChain` sets `_filled[commitment] = msg.sender` immediately, prior to any per-token validation: [2](#0-1) 

The per-output loop then explicitly allows `solverAmount == 0` to skip the transfer/escrow-release logic entirely via `continue`, only marking `isFullyFilled = false`: [3](#0-2) 

This is structurally the same defect as the reported `notify()` bug: an externally reachable function accepts a caller-supplied `amount` (here, each `options.outputs[i].amount`) without a `require(amount > 0, ...)`-style guard, and that unchecked zero value is allowed to flow through and mutate protocol state (`_filled[commitment]`) even though no real value was ever transferred to the beneficiary.

Because `_filled[commitment]` is written unconditionally at the top of the function rather than only after a successful, non-zero fill, calling `fillOrder`/`_fillSameChain` with an all-zero `FillOptions.outputs` array lets any unprivileged address claim the "filler of record" slot for an order's commitment while transferring nothing.

### Impact Explanation
If `_filled[commitment]` is used elsewhere (e.g., to gate a single legitimate filler per commitment, or to determine who is entitled to the escrowed input tokens on eventual full/partial completion or cross-chain settlement), an attacker can front-run or grief real solvers by calling with zero amounts: they capture the attribution slot for free, without paying the beneficiary, potentially diverting or locking the escrowed input assets away from the rightful counterparty. This falls squarely under the "Bridged assets, order escrow... must move exactly once and only to the rightful beneficiary and amount" and "logic attacks" impact categories in the bounty scope, since it lets an unprivileged caller corrupt fill-state without paying the required consideration.

### Likelihood Explanation
`fillOrder`/`_fillSameChain` is a public, unprivileged entry point — any address can call it with a self-chosen `FillOptions.outputs` array containing zero amounts. No relayer, prover, or admin collusion is required, and the zero-amount path is explicitly handled (not merely an oversight that reverts elsewhere) via the `if (remaining == 0 || solverAmount == 0) { ...; continue; }` branch, confirming the code intentionally tolerates `solverAmount == 0` per-token but does not gate the earlier unconditional `_filled[commitment] = msg.sender` write on there being at least one non-zero fill.

### Recommendation
Move `_filled[commitment] = msg.sender` (or otherwise gate write-attribution of the commitment) until after confirming at least one output was actually filled with a non-zero amount, and/or add an explicit `require` that at least one `options.outputs[i].amount > 0` before mutating `_filled`. This mirrors the recommended fix for the seed report: validate `amount > 0` for the meaningful economic input before it is allowed to change protocol state.

### Proof of Concept
Conceptual PoC (exact downstream consequence depends on how `_filled[commitment]` is consumed elsewhere, which I was not able to fully trace in the remaining time):
1. Attacker observes an unfilled order with commitment `C` and one or more output assets.
2. Attacker calls `fillOrder(order, options)` where every `options.outputs[i].amount == 0`.
3. Execution enters `_fillSameChain`, immediately sets `_filled[C] = attacker` at [4](#0-3) .
4. The loop hits `solverAmount == 0` for every output and `continue`s without transferring any tokens to the beneficiary [5](#0-4) .
5. The transaction completes successfully; `_filled[C]` now records the attacker as filler despite zero value delivered.

**Caveat:** I could not, within the available tool calls, confirm every downstream read-site of `_filled[commitment]` (e.g. whether a genuine second filler is blocked, or whether escrow release/cancellation checks this mapping in a way that causes fund loss/lock). If the mapping is only used for last-writer bookkeeping with no exclusivity semantics, the practical impact would be reduced to a state/analytics corruption rather than fund loss — this should be verified against the full `IntentsBase`/`ExtrinsicIntents` consumers of `_filled` before treating this as a confirmed high-severity issue.

### Citations

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L54-79)
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
