## Analysis

The C4 report's core primitive: a party that controls which token address is included in a payout loop can plant a token that reverts on transfer, and because the loop has no per-item isolation, the entire batched payout is blocked — starving a designated recipient of funds while leaving all other functionality untouched.

The strongest Hyperbridge analog is in the **Intent Gateway's escrow-release loop**, not the fee/tax pipeline. `placeOrder` lets any unprivileged user pick arbitrary `order.inputs` token addresses with no allowlist or safety check, confirmed at [1](#0-0) . Those exact tokens are escrowed per-commitment at [2](#0-1) .

When the destination chain's fill is settled back to the source chain, `_withdraw`/`withdraw` iterates every escrowed token for that commitment and pays them **all** to the beneficiary/solver in a single loop with no per-token try/catch — a revert on any one token aborts the whole call, including the release of unrelated legitimate tokens and the accumulated transaction fee: [3](#0-2)  and the equivalent Tron path at [4](#0-3) .

### Title
Escrow-release loop with no per-token isolation lets an order creator lock a solver's proceeds using a single revert-on-transfer input token - (File: `evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
`placeOrder` accepts any arbitrary ERC-20 address as an order input with no validation. `_withdraw`/`withdraw` later releases all escrowed input tokens for that commitment, plus the accumulated fee-token amount, in one loop that reverts entirely if any single token transfer reverts. A malicious order creator can combine one legitimate high-value token with one always-revert-on-transfer token in the same order; once a solver fills it (delivering real output value on the destination chain), the settlement-triggered escrow release on the source chain permanently reverts, locking the solver's entire payout — including the legitimate token and fees — with no recovery path.

### Finding Description
`Order.inputs` is a `TokenInfo[]` fully controlled by the order creator, with token addresses taken at face value: [5](#0-4) . Escrow is credited per-token from these same addresses without any allowlist: [2](#0-1) .

On settlement, `onAccept` authenticates the message and calls `withdraw(body, kind == RequestKind.RefundEscrow)` [6](#0-5) , which loops over `body.tokens` (derived from `order.inputs`) and transfers each to `beneficiary`, reverting the whole call if any transfer fails, then separately releases the escrowed transaction fees to the same beneficiary in the same function: [4](#0-3) . The EVM (non-Tron) analog in `IntentsBase._withdraw` is structurally identical, using `safeTransfer` which reverts on a failed ERC-20 transfer: [7](#0-6) .

Nothing in either path isolates one token's transfer failure from the rest of the batch — there is no try/catch, no partial-success accounting, and no way to retry excluding the bad token, because the commitment's escrow state (`_orders[commitment][token]`) is only decremented inside the same all-or-nothing loop.

### Impact Explanation
A solver who fills a cross-chain order has already irreversibly delivered the output assets on the destination chain (`fillOrder`) before the settlement message triggers `withdraw` on the source chain. If the order creator planted a revert-on-transfer token among their inputs, the entire withdraw call — covering every other legitimately escrowed token for that commitment plus the fee-token payout — permanently reverts. The solver's already-delivered value is unrecoverable, and the escrowed source-chain tokens (including tokens that transfer perfectly fine on their own) become permanently stuck in the contract. This is a direct loss-of-funds condition reachable by any unprivileged order creator against any unprivileged solver, with no relayer, prover, or admin compromise required.

### Likelihood Explanation
`placeOrder` is fully permissionless and performs no validation on input token bytecode/behavior beyond a basic ERC-20 interface call succeeding at escrow time (which a token can satisfy while still reverting selectively later, e.g. once a blacklist flag is flipped, or unconditionally for the withdraw-time transfer target only). Any user can author such a token and place an order with it mixed alongside a real asset; the attack requires no timing races, no governance, and no compromised infrastructure — only a solver willing to fill the order.

### Recommendation
Isolate each token transfer in the escrow-release loop (e.g., wrap each transfer in a low-level call and track per-token success, or move to a pull-based claim model per token) so that a single misbehaving token cannot block release of unrelated escrowed assets or the transaction fee. Consider also validating/allowlisting acceptable input tokens at `placeOrder` time, or requiring the solver to pre-verify transferability of every input token before committing to fill.

### Proof of Concept
1. Attacker deploys `EvilToken`, an ERC-20 whose `transfer`/`transferFrom` succeeds normally except it unconditionally reverts once called from within the IntentGateway's `withdraw` path (or simply always reverts on transfer — since it only needs to work well enough to escrow at `placeOrder` time via `transferFrom(user, gateway, amount)`, which can be special-cased separately from the outbound `transfer` used in `withdraw`).
2. Attacker calls `placeOrder` with `order.inputs = [ {token: USDC, amount: 10_000e6}, {token: EvilToken, amount: 1} ]`, escrowing both per [2](#0-1) .
3. A solver fills the order on the destination chain, delivering the requested output assets to the attacker's beneficiary address.
4. The settlement `RedeemEscrow` message reaches the source chain; `onAccept` calls `withdraw(body, false)` [6](#0-5) .
5. The loop transfers USDC successfully, then attempts to transfer `EvilToken`, which reverts; per [8](#0-7)  the whole `withdraw` call reverts, so the USDC transfer is rolled back too, along with the fee-token release.
6. The solver has already handed over the output assets on the destination chain but can never claim the escrowed USDC or fees on the source chain — the funds are permanently locked, and every retry of the settlement message reverts identically.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L162-163)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable nonReentrant {
        if (order.inputs.length == 0) revert InvalidInput();
```

**File:** evm/src/apps/IntentGatewayV2.sol (L334-343)
```text
        for (uint256 i; i < inputsLen;) {
            address token = address(uint160(uint256(order.inputs[i].token)));
            // Reject duplicate input tokens
            if (_orders[commitment][token] != 0) revert InvalidInput();
            _orders[commitment][token] = reducedInputs[i].amount;

            unchecked {
                ++i;
            }
        }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-417)
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

        if (finalize) {
            uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
            if (fees > 0) {
                delete _orders[body.commitment][TRANSACTION_FEES];
                IERC20(IDispatcher(host()).feeToken()).safeTransfer(beneficiary, fees);
            }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L620-626)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return withdraw(body, kind == RequestKind.RefundEscrow);
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-714)
```text
    function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        _filled[body.commitment] = beneficiary;

        // redeem escrowed tokens
        uint256 len = body.tokens.length;
        for (uint256 i; i < len;) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (_orders[body.commitment][token] == 0) revert UnknownOrder();

            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
                if (!success) revert TransferFailed();
            }

            _orders[body.commitment][token] -= amount;
            unchecked {
                ++i;
            }
        }

        // redeem tx fees
        uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
        if (fees > 0) {
            address feeToken = IDispatcher(host()).feeToken();
            (bool success,) = feeToken.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, fees));
            if (!success) revert TransferFailed();
            delete _orders[body.commitment][TRANSACTION_FEES];
        }
```

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
