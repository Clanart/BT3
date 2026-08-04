Based on the investigation, the strongest verifiable local analog to the seed report's "data validation gap that lets an implicit financial assumption go unchecked" is in the same-chain intent fill path.

### Title
Same-chain partial-fill escrow release assumes `order.inputs` and `order.output.assets` are index-aligned with no on-chain validation - (File: `evm/src/apps/intentsv2/IntrinsicIntents.sol`)

### Summary
The Salty.IO report is fundamentally about an unvalidated assumption (USDS priced at a fixed $1, collateral sold "eventually") that lets the protocol's internal accounting drift from reality. The Hyperbridge analog is in `IntrinsicIntents.sol::_fillSameChain`, which computes escrow releases for a same-chain intent fill by indexing `order.inputs[i]` using the loop counter `i` that iterates over `order.output.assets` (the **output** array), silently assuming the two independently-supplied arrays on the `Order` struct are the same length and positionally paired — an assumption the contract never checks.

### Finding Description
`Order.inputs` (`TokenInfo[]`) and `Order.output.assets` (`TokenInfo[]`, inside `PaymentInfo`) are declared as two independent, user-supplied arrays with no relationship enforced anywhere in the struct or (as far as could be verified) at `placeOrder` time: [1](#0-0) 

In `_fillSameChain`, the per-leg escrow amount is computed by reusing the **output-loop** index `i` to read `order.inputs[i]`: [2](#0-1) 

This implicitly assumes `order.inputs.length == order.output.assets.length` and that `inputs[i]` is "the input that backs output leg `i`." Nothing enforces this pairing. Contrast this with the cross-chain path, `ExtrinsicIntents.sol::_fillCrossChain`, which never indexes `order.inputs` by the output loop at all — it releases the *entire* `order.inputs` array as a single `WithdrawalRequest` after the loop: [3](#0-2) 

and the cancellation path, `_cancelSameChain`, also correctly iterates the *full* `order.inputs.length` independent of any output array: [4](#0-3) 

Only the same-chain partial-fill accounting path bakes in the index-alignment assumption. For any order where `output.assets.length != inputs.length` — a legitimate shape for common intent patterns such as "1 input token backing N output legs" or "N input tokens backing 1 output leg" — the per-leg escrow computation either reads the wrong `TokenInfo` (wrong token/amount pairing) once lengths happen to overlap, or reverts out-of-bounds once `i` exceeds `inputs.length`.

### Impact Explanation
Where `output.assets.length > inputs.length`, filling any leg at index `i >= inputs.length` unconditionally reverts on the out-of-bounds calldata read, permanently preventing that (otherwise valid, escrowed) order from being filled through the intended path. Where `output.assets.length < inputs.length`, only the first `output.assets.length` input entries are ever addressed by `_fillSameChain`; any remaining input tokens beyond that index are never released through the fill path and there is no per-index release available elsewhere for a same-chain order that has already been (partially) filled — only `_cancelSameChain` (owner-only, and only before any fill sets `_filled[commitment]` where relevant) reaches the full input set. Where lengths coincidentally match but the arrays are not truly the pair the user intended, the wrong input token/amount is escrow-released against the wrong output leg's fill accounting, corrupting the `_partialFills` / escrow bookkeeping for that order. This is the same class of bug as the seed report: an unvalidated implicit assumption (index alignment, analogous to "USDS == $1") that the contract's accounting silently depends on, causing funds to be permanently locked or bookkeeping to desynchronize rather than being explicitly rejected at order-placement time.

### Likelihood Explanation
This is reachable through the fully public `placeOrder` → `fillOrder` (same-chain) entrypoints with no privileged actor, malicious relayer, prover, or admin required — any user constructing an `Order` with mismatched `inputs`/`output.assets` lengths (a shape that is otherwise a legitimate and documented use case: 1:N or N:1 swaps) triggers it. I was not able to fully verify, in the time available, whether `placeOrder` performs an explicit length-equality check between `inputs` and `output.assets` before escrowing (the review of `placeOrder`'s implementation was not completed); if such a check exists, this significantly narrows or eliminates the issue. This uncertainty should be resolved before treating the finding as conclusively exploitable.

### Recommendation
**Short term:** Confirm whether `placeOrder` validates `order.inputs.length == order.output.assets.length` (or otherwise documents/enforces the intended input↔output pairing model for same-chain orders). If no such check exists, add one at placement time, or redesign `_fillSameChain`'s escrow-release accounting to be token-keyed (matching the cross-chain path's approach of releasing the full `order.inputs` set, or an explicit mapping) rather than positionally-keyed by the output loop index.

**Long term:** As the seed report recommends, produce a design specification that explicitly states the invariants the `Order` struct's sub-arrays must satisfy for each fill mode (same-chain partial, cross-chain all-or-nothing), and add fuzz/property tests that construct `Order`s with independently-varying `inputs`/`output.assets` lengths to confirm no fill path silently mispairs escrow against the wrong leg or permanently strands escrowed tokens.

### Proof of Concept
1. User calls `placeOrder` with `order.inputs = [TokenInfo(USDC, 100e6)]` (single input) and `order.output.assets = [TokenInfo(WETH, X), TokenInfo(DAI, Y)]` (two output legs) — a natural "swap USDC into two assets" intent.
2. A solver calls `fillOrder` targeting output leg index 0 (WETH): `_fillSameChain` executes with `i = 0`, correctly reads `order.inputs[0]`, and (assuming full fill of leg 0) releases the USDC escrow associated with `inputs[0]`, but this represents the *entire* input, not a share proportional to just the WETH leg.
3. If instead the solver (or a second solver) fills leg index 1 (DAI) first, `_fillSameChain` at `i = 1` attempts `order.inputs[1]`, which is out of bounds for a length-1 `inputs` array, and the entire `fillOrder` transaction reverts — the order can never be filled leg-by-leg in this order, even though escrow was legitimately taken at placement.
4. Verification of exact fund-loss vs. revert-only behavior for all length combinations, and confirmation of whether `placeOrder` blocks this order shape outright, requires running the same-chain fill path in Foundry (`evm/tests/foundry/`) against constructed `Order`s with mismatched `inputs`/`output.assets` lengths — this was not executed as part of this review.

### Citations

**File:** sdk/packages/core/contracts/apps/IntentGatewayV2.sol (L55-77)
```text
struct Order {
    /// @dev The address of the user who is initiating the transfer
    bytes32 user;
    /// @dev The state machine identifier of the origin chain
    bytes source;
    /// @dev The state machine identifier of the destination chain
    bytes destination;
    /// @dev The block number by which the order must be filled on the destination chain
    uint256 deadline;
    /// @dev The nonce of the order
    uint256 nonce;
    /// @dev Represents the dispatch fees associated with the IntentGateway.
    uint256 fees;
    /// @dev Optional session key used to select winning solver.
    address session;
    /// @dev The predispatch information for the order
    /// This is used to encode any calls before the order is placed
    DispatchInfo predispatch;
    /// @dev The tokens that are escrowed for the filler.
    TokenInfo[] inputs;
    /// @dev The filler output, ie the tokens that the filler will provide
    PaymentInfo output;
}
```

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

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L169-180)
```text
        uint256 inputsLen = order.inputs.length;
        TokenInfo[] memory remainingTokens = new TokenInfo[](inputsLen);
        bool hasEscrow = false;
        for (uint256 i; i < inputsLen;) {
            address token = address(uint160(uint256(order.inputs[i].token)));
            uint256 escrowed = _orders[commitment][token];
            if (escrowed > 0) hasEscrow = true;
            remainingTokens[i] = TokenInfo({token: order.inputs[i].token, amount: escrowed});
            unchecked {
                ++i;
            }
        }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L139-147)
```text
        address hostAddr = host();
        bytes memory body = bytes.concat(
            bytes1(uint8(RequestKind.RedeemEscrow)),
            abi.encode(
                WithdrawalRequest({
                    commitment: commitment, tokens: order.inputs, beneficiary: bytes32(uint256(uint160(msg.sender)))
                })
            )
        );
```
