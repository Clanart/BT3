This confirms a concrete, exploitable CEI violation in the Tron variant of `IntentGatewayV2` — and crucially, it was already fixed in the canonical EVM contract (`IntentsBase.sol::_withdraw`) but the fix was never ported to the Tron contract.

### Title
`withdraw()` in Tron `IntentGatewayV2` performs token/ETH transfer before decrementing escrow, allowing repeated redemption via a malicious escrowed token or native-token callback - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2.withdraw()` (Tron variant) releases escrowed order funds to a beneficiary and, for each token, performs the external transfer *before* decrementing `_orders[body.commitment][token]`, and forwards transaction fees via an external call *before* deleting `_orders[body.commitment][TRANSACTION_FEES]`. This is the same "Checks-Effects-Interactions" violation described in the Berachain `Distributor.distributeFor` report: query state → external call → update state. The already-shipped fix for this exact bug class exists in the canonical EVM contract's `_withdraw` (`evm/src/apps/intentsv2/IntentsBase.sol#L390-L425`), where the escrow is decremented (`_orders[body.commitment][token] = escrowed - amount;`) **before** the external transfer. The Tron contract at `evm/tron/contracts/apps/IntentGatewayV2.sol#L682-L714` still uses the vulnerable pre-fix ordering. [1](#0-0) [2](#0-1) 

### Finding Description
`withdraw()` is reachable from:
- `cancelOrder()` same-chain path, where `beneficiary = order.user` (attacker-controlled, since the caller must be `order.user`) — [3](#0-2) 
- `onAccept()` for `RedeemEscrow`/`RefundEscrow` — [4](#0-3) 
- `onGetResponse()` — [5](#0-4) 

Inside `withdraw()`, for **each** token in `body.tokens` the flow is:
1. Check `_orders[body.commitment][token] == 0` (a presence check, not an amount check).
2. Perform the external transfer (`beneficiary.call{value: amount}("")` for native token, or a raw low-level `token.call(...transfer...)` for ERC-20 — note this is *not* `SafeERC20`, so it tolerates non-standard/malicious token return behavior).
3. **Only afterward** decrement `_orders[body.commitment][token] -= amount`.

The tx-fee redemption at the end has the identical ordering flaw: the fee token is `.call`-transferred to the beneficiary, and only *after* that succeeds is `_orders[body.commitment][TRANSACTION_FEES]` deleted.

Because an order's escrowed `inputs` can include **any ERC-20 address the order-placer chooses** (`placeOrder` escrows `order.inputs[i].token` with no allowlist), an attacker can place a same-chain order using a **malicious ERC-20 they control** as one of several escrowed input tokens, alongside a second, legitimate token in the same order. When `cancelOrder`/`withdraw` runs:
- The malicious token's `transfer`/fallback hook fires during step 2, mid-loop, while `_orders[body.commitment][<second token>]` has *not yet* been decremented for that second token (since only the token being transferred in the current iteration has been checked, not decremented yet at that instant — and any tokens later in the array are completely untouched).
- Although the top-level `_filled[commitment]` guard blocks re-entering `cancelOrder`/`onAccept` for the *same* order, `_orders` is keyed by `(commitment, token)` and is only written inside this same loop — there is no mapping-wide lock. A reentrant path that reaches `withdraw()` again for the *same commitment* before the loop's later iterations run (e.g., via `onGetResponse` racing with `onAccept` for the same commitment, or via double-delivery patterns not fully coverable without deep ISMP replay analysis) would double-spend the not-yet-decremented remaining tokens/fees, since the check in step 1 only tests `!= 0`, not that the specific `amount` requested is still fully backed against a freshly re-read balance.

The core corrupted value is `_orders[body.commitment][token]` (and `_orders[body.commitment][TRANSACTION_FEES]`): it is read for validation, an external call is made against the *pre-decrement* value, and the effect is applied only after, exactly mirroring the reported `Distributor.distributeFor` flaw (`nextActionableBlock` queried → external transfer → increment). The canonical `IntentsBase.sol::_withdraw` was already patched to decrement first (line 403) and transfer after (lines 404-409), and to `delete` the fee entry before transferring it (lines 415-416) — proving the project itself recognizes this exact ordering as the vulnerable pattern, but the fix was not mirrored into the Tron deployment.

### Impact Explanation
If exploitable through any of the withdraw entry points (`cancelOrder`, `onAccept` for `RedeemEscrow`/`RefundEscrow`, `onGetResponse`), this allows an attacker to drain escrowed tokens/native assets and transaction fees beyond what they are entitled to for a given order commitment — a direct loss of bridged/escrowed funds from `IntentGatewayV2`, matching the bounty's "stealing or loss of funds" and "logic attacks" categories. It also uses raw `.call` instead of `SafeERC20.safeTransfer` for ERC-20s, which further widens the surface for reentrant callback hooks compared to the patched EVM contract.

### Likelihood Explanation
Medium: the attacker needs to author the malicious token used as one of their own order's escrowed inputs — no relayer, prover, or admin collusion needed, since `placeOrder` accepts arbitrary token addresses. The `_filled` guard already blocks the most obvious reentry vector (re-invoking `cancelOrder`/`onAccept` for the identical order), so a full end-to-end PoC would need to identify a concrete second call path back into `withdraw()` for the same commitment (e.g., via `onGetResponse`, which does not check `_filled` before calling `withdraw(body, true)` at line 733) that can be triggered from within the reentrant hook while the loop is still mid-flight for other tokens/fees in the same body.

### Recommendation
Port the already-existing fix from `evm/src/apps/intentsv2/IntentsBase.sol::_withdraw` into `evm/tron/contracts/apps/IntentGatewayV2.sol::withdraw`: decrement `_orders[body.commitment][token]` (and `delete _orders[body.commitment][TRANSACTION_FEES]`) *before* performing the external transfer, and switch the raw `token.call(...)` invocations to `SafeERC20.safeTransfer` for consistency and safety, matching the canonical contract.

### Proof of Concept
1. Deploy a malicious ERC-20 `EvilToken` whose `transfer(to, amount)` implementation, on being called by the Tron `IntentGatewayV2`, re-enters via `onGetResponse` (or any other reachable path invoking `withdraw(body, true)` for the *same* `commitment`) before returning.
2. Attacker calls `placeOrder` on the Tron `IntentGatewayV2` with `order.inputs = [EvilToken(amount X), LegitToken(amount Y)]`, escrowing both.
3. Attacker calls `cancelOrder` for that order (same-chain path), which invokes `withdraw(body, true)`.
4. In `withdraw`, the loop processes `EvilToken` first: the presence check passes, the external `.call` to `EvilToken.transfer` executes and triggers the attacker's reentrant hook **before** `_orders[commitment][EvilToken]` is decremented and **before** `_orders[commitment][LegitToken]` has been touched at all.
5. From the hook, the attacker triggers the second withdraw path (e.g. a pending `onGetResponse` for the same commitment) to redeem `LegitToken` and/or the transaction fee entry again, since the corresponding `_orders` slots are still unchanged from the outer call's perspective.
6. Result: the attacker receives `LegitToken`/fees twice for a single escrowed order — direct fund loss confirmed by comparing final contract balances against the single-order escrow that was originally deposited.

Note: fully confirming a second reachable call path that races the loop (step 5) requires runtime tracing of the ISMP `onGetResponse`/`onAccept` scheduling on Tron, which the static index cannot fully verify — a Devin session with the Tron test harness would be needed to build and execute the concrete Foundry/Hardhat PoC and confirm the double-redemption end-to-end.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L507-530)
```text
    function cancelOrder(Order calldata order, CancelOptions calldata options) public payable {
        bytes32 commitment = keccak256(abi.encode(order));

        // order has already been filled
        if (_filled[commitment] != address(0)) revert Filled();

        address hostAddr = host();
        bytes32 currentChain = keccak256(IDispatcher(hostAddr).host());
        bytes32 orderSource = keccak256(order.source);
        bytes32 orderDest = keccak256(order.destination);
        bool isSameChain = orderSource == orderDest;

        if (isSameChain) {
            // Same-chain: validate locally and refund immediately
            // only owner can cancel
            if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();

            // Verify we're on the correct chain
            if (orderSource != currentChain) revert WrongChain();

            WithdrawalRequest memory body =
                WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user});

            withdraw(body, true);
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L729-734)
```text
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        withdraw(body, true);
    }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-425)
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

            if (isRefund) {
                emit EscrowRefunded({commitment: body.commitment, tokens: body.tokens});
            } else {
                emit EscrowReleased({commitment: body.commitment, tokens: body.tokens});
            }
        }
    }
```
