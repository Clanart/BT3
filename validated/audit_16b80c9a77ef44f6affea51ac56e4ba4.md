## Analysis

I found a solid local analog to the `publicMint` DoS-with-failed-call pattern in the Intent Gateway's escrow release logic.

### Title
Griefing via Malicious Escrowed Input Token Permanently Blocks Release of Bundled Legitimate Escrow — ([File: evm/src/apps/intentsv2/IntentsBase.sol])

### Summary
`_withdraw` in `IntentsBase.sol` and its Tron counterpart `withdraw` in `evm/tron/contracts/apps/IntentGatewayV2.sol` release every escrowed input token of an order in a single atomic loop of external calls (`IERC20.safeTransfer` / raw `.call`). An order's `inputs` array is chosen entirely by the unprivileged order creator at `placeOrder` time and can include an arbitrary ERC20 address the creator controls. If any single token transfer in that loop reverts, the whole `withdraw()`/`_withdraw()` call reverts — permanently blocking release of every *other*, otherwise-transferable token bundled in that same order/commitment.

### Finding Description
`_withdraw` iterates `body.tokens` and calls `IERC20(token).safeTransfer(beneficiary, amount)` for each entry, with no isolation between tokens: [1](#0-0) 

The Tron variant is functionally identical, using low-level `.call` and reverting with `TransferFailed()`: [2](#0-1) 

This function is invoked from `onAccept()` for both `RedeemEscrow` (solver claiming escrow after filling on the destination chain) and `RefundEscrow` (user cancellation), and from `onGetResponse()` for the storage-proof cancellation path: [3](#0-2) 

The order's input token list is fully attacker-controlled at order-placement time — a user can escrow both a legitimate token (e.g. USDC) and a token they own with a revocable transfer path (pause/blacklist) as separate `inputs[]` entries in the same order. A solver, evaluating the order by its total value, fills it and irrevocably delivers real output tokens on the destination chain, then the `RedeemEscrow` message is dispatched back to the source chain to release the solver's reward. Before that message is processed, the attacker disables transfers on their own malicious token. When `_withdraw` reaches that token in the loop, `safeTransfer` reverts, which reverts the entire `onAccept()` call. `EvmHost.dispatchIncoming` catches this failure and deletes the request receipt "so that it can be retried": [4](#0-3) 

But the retry will fail identically forever, since the attacker permanently controls the poison token's transferability. Because the release is atomic across *all* tokens in the order, the solver's legitimately-transferable escrowed reward (e.g. USDC) is locked alongside the poison token, even though nothing is wrong with that token itself.

### Impact Explanation
The solver has already delivered real value (the output tokens) on the destination chain before this failure occurs on the source chain. The griefing attacker (an ordinary, unprivileged order creator) can cause permanent loss/lock of the solver's rightful escrowed reward by combining one malicious/revocable token with legitimate tokens in a single order. This is fund loss for an innocent counterparty caused entirely by unprivileged, local contract logic — no relayer, prover, or admin compromise is required, matching the bounty's "stealing or loss of funds" and "bridged assets ... must move exactly once and only to the rightful beneficiary" criteria.

### Likelihood Explanation
Any user can place an order today with `inputs[]` containing a token contract they deploy and control (pausable, blacklistable, or simply self-destructing its own `transfer` logic). No special privilege, timing race with relayers, or governance action is needed — the attacker only needs to withhold transferability of their own token at the moment the redemption/refund message is processed, which they fully control.

### Recommendation
Isolate per-token transfers in `_withdraw`/`withdraw` so a single failing token cannot block release of the others — e.g., wrap each transfer in a try/catch (or low-level call already used in the Tron variant) and, on failure, credit the beneficiary a claimable/pull-based balance for that specific token instead of reverting the whole loop. This mirrors the report's recommended fix of isolating external calls and using a pull-over-push pattern.

### Proof of Concept
1. Attacker deploys `PoisonToken`, an ERC20 they fully control with a `transfer` function that can be switched to always revert (e.g. via a `paused` flag only the attacker can set).
2. Attacker calls `placeOrder` with `inputs = [ {token: USDC, amount: X}, {token: PoisonToken, amount: 1} ]` and an attractive `output` (e.g. requiring less value than the escrowed USDC, to lure a solver).
3. A solver fills the order via `fillOrder`, delivering the output tokens to the beneficiary on the destination chain, and the contract dispatches a `RedeemEscrow` `WithdrawalRequest` back to the source chain naming the solver as beneficiary for both `USDC` and `PoisonToken`.
4. Before the message is processed on the source chain, the attacker sets `PoisonToken.paused = true`.
5. When the relayer delivers the `RedeemEscrow` message, `onAccept` → `_withdraw` transfers `USDC` successfully, then reaches `PoisonToken.safeTransfer`, which reverts.
6. The whole `onAccept` call reverts; `EvmHost.dispatchIncoming` deletes the receipt for retry, but every subsequent retry reverts identically since `PoisonToken` stays paused.
7. The solver's escrowed `USDC` reward remains permanently locked in the contract despite USDC itself being fully functional, while the solver has already given away the output tokens on the destination chain.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L394-410)
```text
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L686-705)
```text
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
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L289-295)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            _authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return _withdraw(body, kind == RequestKind.RefundEscrow, true);
        }
```

**File:** evm/src/core/EvmHost.sol (L808-817)
```text

        (bool success,) = address(destination)
            .call(abi.encodeWithSelector(IApp.onAccept.selector, IncomingPostRequest(request, relayer)));

        if (!success) {
            // so that it can be retried
            delete _requestReceipts[commitment];
            return;
        }
        emit PostRequestHandled({commitment: commitment, relayer: relayer});
```
