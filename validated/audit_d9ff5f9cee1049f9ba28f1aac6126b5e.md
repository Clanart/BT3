Based on my research, I found a concrete local analog in the Intent Gateway's same-chain partial-fill logic, where cumulative fill state is tracked by a shared key (output token address) while escrow release is computed per array index — mirroring the "combining state that should be tracked independently" bug class from the LSP6 report.

### Title
Duplicate output-token entries in an order let a solver finalize a fill while leaving one input's escrow permanently locked, unrecoverable via cancel - (File: evm/src/apps/intentsv2/IntrinsicIntents.sol)

### Summary
`_fillSameChain` tracks cumulative partial-fill progress in `_partialFills[commitment][outputToken]`, keyed only by **token address**, while it derives each per-fill escrow release from `order.inputs[i]` keyed by **array index** [1](#0-0) . If an order's `output.assets` array contains two entries with the same output token, the second entry's "already filled" lookup collides with the first, causing the loop to treat the second output as already satisfied (`remaining == 0`) and `continue` without ever populating `escrowedInputs[i]` for that index [2](#0-1) .

### Finding Description
In `_fillSameChain`:
- `alreadyFilled = _partialFills[commitment][outputToken]` reads a per-token cumulative counter [3](#0-2) .
- When two output entries at different indices `i` share the same `outputToken`, satisfying the first entry sets `_partialFills[commitment][outputToken] = totalRequired` [4](#0-3) .
- On the second entry (different index, but same token), `remaining = totalRequired - alreadyFilled` evaluates to `0` (assuming matching `totalRequired`), so the loop hits `continue` at line 78 without setting `escrowedInputs[i]` (it stays the zero-valued default) and without flipping `isFullyFilled` to false, since the false-condition only triggers when `solverAmount == 0 && remaining > 0` [5](#0-4) .
- The order therefore finalizes as `isFullyFilled = true`, `_filled[commitment]` is set to the solver [6](#0-5) [7](#0-6) , and `OrderFilled` fires — but `_withdraw` skips any `escrowedInputs[i]` entry whose `amount == 0` [8](#0-7) , so the corresponding `_orders[commitment][inputs[i].token]` escrow balance is never decremented and never transferred out.
- `_cancelSameChain` does not check whether the order was already finalized/filled before allowing a refund of "remaining" escrow [9](#0-8) , so whether this locked balance is permanently stuck or double-claimable via cancel depends on caller-side guards in the public `fillOrder`/`cancelOrder` entrypoints, which I was not able to inspect before running out of iterations (only the internal `_fillSameChain`/`_cancelSameChain` helpers were confirmed).

### Impact Explanation
This is the direct local analog of the "adding permission twice" bug: state meant to be tracked per-entry is instead combined by a coarser shared key (token address instead of array index), producing an incorrect combined result that lets one escrow bucket bypass release/refund accounting entirely. Depending on the unverified caller-side guard around `_filled`, this results in either (a) permanent lock of a legitimate user's escrowed funds with no path to refund, or (b) if `cancelOrder` can still be invoked post-fill, a double-payout — the solver already received escrow for the filled index while the user could also reclaim the never-decremented `_orders[commitment][inputs[1].token]` balance via cancellation, i.e. duplicate settlement of the same commitment.

### Likelihood Explanation
Constructing the trigger condition requires an order with duplicate `token` entries in `output.assets`, which is entirely controlled by the order creator (a normal user, not a privileged actor) at `placeOrder` time — no malicious relayer, prover, or admin is needed. Whether it is exploitable for profit (vs. only self-inflicted fund lock) hinges on whether the order creator and the filling solver can coordinate (or be the same actor) to extract the un-decremented escrow afterward, which requires confirming the `fillOrder`/`cancelOrder` public entrypoint guards that I could not fully verify in the time available.

### Recommendation
Track partial-fill progress per output index (or per `(commitment, index)`) rather than per output token, and validate at fill time that `escrowedInputs[i]` is only ever left as a zero placeholder when the corresponding output was already fully released in the *same* index's prior state, not a same-token sibling index. Additionally, `_cancelSameChain` should explicitly reject any commitment whose `_filled[commitment]` is already non-zero, and `_fillSameChain` should require `_filled[commitment] == address(0)` at entry (or equivalent guard) so a finalized order can never be reprocessed.

### Proof of Concept
Conceptual sequence (exact PoC requires the public `fillOrder` entrypoint and `Order`/`FillOptions` encoding not fully retrieved in this session):
1. User places an order with `inputs = [{tokenA, 100}, {tokenB, 50}]` and `output.assets = [{token: T, amount: X}, {token: T, amount: X}]` (duplicate output token/amount).
2. Solver calls fill with `options.outputs = [{T, X}, {T, X}]`.
3. Iteration `i=0`: `alreadyFilled=0`, fully fills, sets `_partialFills[commitment][T] = X`, releases full `tokenA` escrow to solver.
4. Iteration `i=1`: `alreadyFilled = X == totalRequired`, so `remaining == 0` → `continue`; `escrowedInputs[1]` stays `{0,0}`; `isFullyFilled` remains `true`.
5. `_withdraw` skips the zero-amount entry, so `tokenB`'s escrow (`_orders[commitment][tokenB]`) is never released, while `_filled[commitment]` is already set — locking `tokenB`'s escrow with `OrderFilled` having already fired.

Confirming whether this is exploitable for direct fund theft (vs. self-griefing) requires reviewing the public `fillOrder`/`cancelOrder` functions and their `_filled`-state guards, which I could not complete before this session ended.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L148-152)
```text
    /**
     * @dev Maps (commitment, output token) to the cumulative amount already filled.
     * Used to track partial fill progress for same-chain orders.
     */
    mapping(bytes32 => mapping(bytes32 => uint256)) public _partialFills;
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-392)
```text
    function _withdraw(WithdrawalRequest memory body, bool isRefund, bool finalize) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        if (finalize) _filled[body.commitment] = beneficiary;
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L396-398)
```text
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (amount == 0) continue;
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L57-57)
```text
        _filled[commitment] = msg.sender;
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

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L97-98)
```text
            uint256 amountFilled = alreadyFilled + fillAmount;
            _partialFills[commitment][outputToken] = amountFilled;
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
