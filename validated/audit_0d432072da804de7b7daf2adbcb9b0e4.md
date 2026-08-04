### Title
Mismatched loop index in `_fillSameChain` pairs `order.output` entries with the wrong `order.inputs` entries when array lengths differ, causing escrow tokens to be permanently locked (or the fill to revert) - ([File: evm/src/apps/intentsv2/IntrinsicIntents.sol])

### Summary
`_fillSameChain` iterates over `order.output.assets` using index `i` (bounded by `outputsLen = order.output.assets.length`) but reads `order.inputs[i]` inside the same loop to compute the escrow amount to release. This assumes `order.inputs.length == order.output.assets.length`, an invariant that is never validated anywhere in `placeOrder`. This is structurally the same class of bug as the reported `ClosePositionArgs` copy-paste: a single loop/index variable is reused across two arrays that are supposed to be handled independently, without validating they line up.

### Finding Description
`placeOrder` in `IntentGatewayV2.sol`/`IntentsBase.sol` accepts an arbitrary user-supplied `Order` struct with independent `order.inputs` (escrowed collateral) and `order.output.assets` (required solver payout) arrays — nothing enforces `order.inputs.length == order.output.assets.length`.

`_fillSameChain` then does: [1](#0-0) 

and later, inside the `for (uint256 i; i < outputsLen; i++)` loop: [2](#0-1) 

`i` ranges over `order.output.assets.length`, but `order.inputs[i]` is dereferenced with the same `i`. Two failure modes result:

1. If `order.output.assets.length > order.inputs.length`, `order.inputs[i]` goes out of bounds once `i` reaches `order.inputs.length`, reverting the solver's fill transaction (wasted gas / griefing of solvers attempting to fill such an order).
2. If `order.output.assets.length < order.inputs.length`, the loop only ever touches `order.inputs[0 .. outputsLen-1]`. `escrowedInputs` is sized to `outputsLen`, so any input token at index `>= outputsLen` is never added to the `WithdrawalRequest.tokens` array passed to `_withdraw`: [3](#0-2) 

Once the fill is full (`isFullyFilled == true`), `_filled[commitment]` is set to the solver, finalizing the order. The un-indexed excess input tokens remain recorded in `_orders[commitment][token]` but there is no longer any code path to release them: cancellation is unconditionally blocked once an order is filled, since every cancel path checks `_filled[commitment]` first (see `IntentGatewayV2` cancel entrypoint / `_cancelSameChain`, which requires the commitment be unfilled). The tokens are permanently stranded in the contract.

### Impact Explanation
This breaks the required invariant that "order escrow ... must move exactly once and only to the rightful beneficiary and amount." Escrowed input tokens beyond `order.output.assets.length` are silently excluded from release and become irrecoverably locked in the `IntentGatewayV2` contract once the order finalizes — a direct on-chain loss of user funds. It is a public-entrypoint path (`placeOrder` + `fillOrder`, both callable by any unprivileged account) that requires no relayer, prover, or admin involvement, and produces incorrect/incomplete fund movement purely from a length mismatch that is never validated.

### Likelihood Explanation
Likelihood is moderate: it requires an order whose `inputs.length` differs from `output.assets.length`, which is entirely legal per the `Order`/`TokenInfo[]` struct definitions and is not rejected by `placeOrder`. Any user (intentionally or by a wallet/integration bug) constructing such an order and having it filled same-chain triggers the lock; no attacker collusion, malicious relayer, or privileged actor is needed — only a normal `fillOrder` call by a solver.

### Recommendation
- In `placeOrder`, enforce `order.inputs.length == order.output.assets.length` (revert with `InvalidInput()` otherwise), matching the implicit 1:1 pairing assumption used later in `_fillSameChain`.
- Defensively, in `_fillSameChain`, iterate/validate both arrays' lengths before indexing `order.inputs[i]` with the outputs-loop index, and ensure all escrowed tokens are accounted for and released (or explicitly refunded) when the order is marked filled.

### Proof of Concept
1. Attacker (or a buggy integrator) calls `placeOrder` with:
   - `order.inputs = [ {token: USDC, amount: 1000e6}, {token: DAI, amount: 500e18} ]` (2 inputs)
   - `order.output.assets = [ {token: WETH, amount: 1e18} ]` (1 output)
   - Same-chain order (`order.source == order.destination`).
   Both USDC and DAI are transferred into escrow (`_orders[commitment][USDC] += 1000e6`, `_orders[commitment][DAI] += 500e18`).
2. A solver calls `fillOrder` providing `1e18` WETH to the beneficiary.
3. In `_fillSameChain`, `outputsLen = 1`, so the loop only runs for `i = 0`, computing `escrowedInputs[0]` from `order.inputs[0]` (USDC). `isFullyFilled` becomes `true` since the single output requirement is met.
4. `_withdraw` releases only the escrowed USDC to the solver; `_filled[commitment]` is set, finalizing the order.
5. The escrowed `500e18` DAI recorded at `_orders[commitment][DAI]` is never released to anyone. Any subsequent call to cancel the order reverts because `_filled[commitment] != address(0)`, permanently locking the DAI in the `IntentGatewayV2` contract.

Note: I did not find any length-equality check for `order.inputs`/`order.output.assets` in `IntentsBase.sol`, `IntrinsicIntents.sol`, or `ExtrinsicIntents.sol`, nor in the `placeOrder` implementation reviewed; if such a check exists elsewhere (e.g., enforced only at an off-chain SDK/UI layer), that would not prevent a direct contract call from a script or non-SDK integration and the on-chain invariant would still be missing.

### Citations

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L54-65)
```text
    function _fillSameChain(Order calldata order, FillOptions calldata options, bytes32 commitment) internal {
        uint256 outputsLen = order.output.assets.length;

        _filled[commitment] = msg.sender;

        uint256 msgValue = msg.value;
        address beneficiary = address(uint160(uint256(order.output.beneficiary)));
        bool isFullyFilled = true;

        TokenInfo[] memory escrowedInputs = new TokenInfo[](outputsLen);
        TokenInfo[] memory outputFills = new TokenInfo[](outputsLen);

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

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L131-142)
```text
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
        }
```
