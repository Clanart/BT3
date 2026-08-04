## Confirmed: `_orders[commitment][token]` is keyed only by token address, not by output-pair index

This confirms the mapping structure at [1](#0-0)  — escrow is tracked per `(commitment, token)`, aggregated across all input entries sharing that token, not per output-pair index.

### Title
Same-chain partial-fill escrow release drains the whole token bucket instead of the completing pair's share when an order has duplicate input tokens across output pairs - (File: `evm/src/apps/intentsv2/IntrinsicIntents.sol`)

### Summary
`_fillSameChain` releases escrow proportionally per output-pair index `i` using `order.inputs[i].amount * fillAmount / totalRequired`, but only for the *non-final* fill of that pair. Once a pair's cumulative fill (`amountFilled`) reaches `totalRequired`, the code switches to releasing `_orders[commitment][token]` in full — the entire remaining balance stored under that token key, not the amount attributable to pair `i`: [2](#0-1) .

### Finding Description
Escrow accounting in `IntentsBase` is keyed solely by `(commitment, token address)`: [1](#0-0) . If an order's `inputs[]` array contains the same token at two different indices (e.g. `inputs[0].token == inputs[1].token == USDC`, paired against two independent output assets in `output.assets[0]` and `output.assets[1]`), both escrow amounts accumulate into the single `_orders[commitment][USDC]` slot at `placeOrder` time.

During `_fillSameChain`, each output pair `i` is processed independently with its own `totalRequired`, `alreadyFilled`, and `_partialFills[commitment][outputToken]` tracker: [3](#0-2) . When pair `i`'s cumulative fill reaches `totalRequired` for the *first* time (`amountFilled == totalRequired`), the contract does not compute `i`'s proportional share of `_orders[commitment][USDC]` — it reads and later releases the **entire current balance** of `_orders[commitment][USDC]`: [2](#0-1) . This is the same class of bug as the report's `removePriceImpactOpenInterest()`: a "completion" branch removes/releases based on the full remaining pool rather than `min(delta, remaining allocated to this sub-position)`.

If pair 0 (mapped to USDC) completes while pair 1 (also mapped to USDC, but not yet filled) still has its portion sitting in the same `_orders[commitment][USDC]` bucket, the solver completing pair 0 receives pair 1's still-escrowed USDC as well via `_withdraw` — `_withdraw` simply transfers whatever `escrowedAmount` was computed and decrements the shared balance: [4](#0-3) . The user's second output pair is then left with insufficient/zero backing escrow, so its solver (or the user's cancellation, which sweeps whatever remains under `_orders[commitment][token]`: [5](#0-4) ) recovers nothing for the pending pair.

### Impact Explanation
This is unauthorized transaction/value manipulation within the Intent Gateway's escrow custody: a solver who completes one output pair first can receive tokens escrowed for a *different, still-pending* output pair on the same order, at the expense of the user (loss of funds) or a later solver (who fills the remaining pair but the gateway can no longer pay them the correct proportional input, since the shared bucket has already been drained). This falls squarely under "stealing or loss of funds" / "wrong beneficiary or amount" per the bounty's impact gate, exercised entirely through the public `fillOrder` entrypoint by an ordinary, unprivileged solver — no relayer, prover, or admin involvement required.

### Likelihood Explanation
Likelihood is Medium: it requires a user (or a solver-crafted/predispatch order that a user signs) to place a same-chain order whose `inputs[]` array repeats a token across two or more indices paired with independent output assets. Nothing in `placeOrder`/`IntentsBase` appears to reject or deduplicate repeated input tokens (not shown enforced in the reviewed code), so this is a valid, unprivileged order shape, but it is not the "typical" order (most orders use one input token per fill). This mirrors the original report's own likelihood rating (Medium) — the trigger condition is a specific but reachable state, not requiring any privileged actor.

### Recommendation
Track escrow per output-pair index (or per `(commitment, inputIndex)`), not solely by token address, so that a completing pair only ever releases the amount it independently escrowed. Alternatively, when `amountFilled == totalRequired`, compute the pair's fair share as `min(order.inputs[i].amount, _orders[commitment][token] - amountAlreadyReleasedForOtherPairsOnThisToken)` rather than reading the full aggregate bucket, mirroring the report's recommended `min(deltaOiUsd, positionSizeUsd - expiredOiUsd)` fix.

### Proof of Concept
1. User places a same-chain order with `inputs = [{USDC, 500}, {USDC, 500}]` and `output.assets = [{DAI, 500e18} (pair 0), {WETH, 1e18} (pair 1)]`. At placement, `_orders[commitment][USDC] = 1000` (500 + 500 aggregated under the same key).
2. Solver A fully fills pair 0 by providing `500e18` DAI. In `_fillSameChain`, for `i = 0`: `totalRequired = 500e18`, `fillAmount = 500e18`, `amountFilled == totalRequired` → `escrowedAmount = _orders[commitment][USDC]` = **1000** (the full aggregate, not the 500 that belongs to pair 0): [6](#0-5) .
3. `isFullyFilled` is still `false` (pair 1 unfilled), so `_withdraw(body, false, false)` releases the full 1000 USDC to Solver A and `_orders[commitment][USDC]` becomes 0: [4](#0-3) .
4. Solver B attempts to fill pair 1 (WETH leg) — `_orders[commitment][USDC]` is now 0, so the WETH-side fill can no longer be paid its input allocation; the user's pair-1 escrow is gone despite pair 1 never being filled, resulting in fund loss/lock for the user and an over-payment to Solver A.

**Uncertainty note:** I was not able to fully verify, within the available index, whether `placeOrder` (in `IntentGatewayV2.sol`, not fully retrieved due to index truncation) contains an implicit validation that rejects duplicate `inputs[].token` entries across pairs. If such a check exists elsewhere in the placement path, this specific trigger would be blocked and the finding should be treated as unconfirmed. A full Devin session with complete repository access would be needed to definitively confirm or rule out that guard before treating this as exploitable in production.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L136-140)
```text
    /**
     * @dev Maps (commitment, token address) to the escrowed amount for that token.
     * Decremented as tokens are released via fills or refunds.
     */
    mapping(bytes32 => mapping(address => uint256)) public _orders;
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

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L70-98)
```text
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

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L161-187)
```text
    function _cancelSameChain(Order calldata order, bytes32 commitment) internal {
        if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();

        address hostAddr = host();
        bytes32 currentChain = keccak256(IDispatcher(hostAddr).host());
        bytes32 orderSource = keccak256(order.source);
        if (orderSource != currentChain) revert WrongChain();

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
        if (!hasEscrow) revert UnknownOrder();

        WithdrawalRequest memory body =
            WithdrawalRequest({commitment: commitment, tokens: remainingTokens, beneficiary: order.user});

        _withdraw(body, true, true);
    }
```
