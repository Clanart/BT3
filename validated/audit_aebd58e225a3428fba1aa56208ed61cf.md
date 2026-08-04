Confirmed: there's no input-token allowlist in `IntentsBase`/`ExtrinsicIntents` — `placeOrder` accepts any `TokenInfo[]` the user supplies as escrow inputs, with no validation beyond duplicate-token rejection.

### Title
Malicious input token in a cross-chain intent order permanently DoS's escrow redemption, allowing the order creator to steal a solver's cross-chain fill payment - (File: `evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
`IntentsBase._withdraw()` releases every escrowed token for an order commitment inside a single `for` loop, transferring each `body.tokens[i]` to the beneficiary with `IERC20(token).safeTransfer` [1](#0-0) . An order creator fully controls the `inputs` array passed into `placeOrder` — there is no token allowlist — so they can escrow a legitimate valuable token together with an attacker-controlled "poison" token in the same order [2](#0-1) . When a solver fills the order cross-chain, `_fillCrossChain` dispatches a single `RedeemEscrow` request containing the *full* `order.inputs` list back to the source chain [3](#0-2) , which on `onAccept` calls `_withdraw` for all tokens atomically [4](#0-3) . If any single token transfer in that loop reverts (e.g. a token the attacker can pause, blacklist the gateway/solver address on, or that simply reverts on transfer), the entire `_withdraw` call reverts, and since the whole cross-chain message delivery is atomic, none of the escrow — including the legitimate token — is ever released to the solver.

### Finding Description
The corrupted invariant: "escrow release for a fill is all-or-nothing across every token in the order, gated by the worst-behaved token in that same order." This mirrors the reported MultiRewards bug exactly — a loop that must fully succeed across an attacker-influenced list of token addresses, where one bad actor token blocks payout of all the good ones for every party depending on that list.

Concretely:
1. The attacker calls `placeOrder` with `order.inputs = [ {token: USDC, amount: X}, {token: POISON, amount: 1} ]`, where `POISON` is an ERC-20 the attacker controls (pausable, blacklist-capable, or simply reverting on `transfer` to specific addresses) [2](#0-1) . Both tokens get escrowed into `_orders[commitment][token]` [5](#0-4) .
2. A solver, evaluating only the *output* side of the order (what they must pay on the destination chain), fills the order cross-chain, transferring real output assets to the beneficiary and dispatching the `RedeemEscrow` request that carries `order.inputs` verbatim [3](#0-2) .
3. Before or after the fill, the attacker pauses/blacklists `POISON` for the gateway or the solver's address (something entirely within the attacker's own token's admin control, not requiring any Hyperbridge privilege).
4. When the `RedeemEscrow` message lands on the source chain, `onAccept` → `_withdraw` iterates `body.tokens`; the `POISON` transfer reverts, unwinding the entire transaction, including the `USDC` transfer that would otherwise have paid the solver [6](#0-5) .
5. Because the message body (and thus the ISMP commitment) is fixed by the original order, every redelivery attempt of the same request replays the identical failing loop — there is no per-token skip, no way to strip `POISON` from the withdrawal, and no governance lever to remove a token from a specific order's `inputs` list once dispatched.

Existing guards do not stop this: `placeOrder` only rejects *duplicate* input tokens, not malicious ones [7](#0-6) ; `_withdraw` has no try/catch or skip-on-failure logic around each token transfer, unlike the fix pattern (low-level call with gas cap) applied to the analogous MultiRewards bug.

### Impact Explanation
High. The solver already paid real value (the order's output assets) on the destination chain before attempting to redeem the source-chain escrow. A permanently reverting `_withdraw` means the solver can never claim the input escrow (including the legitimate, valuable token that was bundled with the poison token), resulting in an unrecoverable loss of the solver's fill payment and permanent lock of the escrowed principal. The same mechanism also blocks legitimate order cancellation/refund paths (`RefundEscrow` uses the identical `_withdraw` loop) [8](#0-7) , so funds can be locked indefinitely with no recovery path.

### Likelihood Explanation
Medium-to-High. Unlike the MultiRewards case (which needs a compromised governance-whitelisted token), here the attacker is the *unprivileged order creator* and needs no privileged role at all — they simply pick their own token as one of the order's inputs and control that token's pausability/blacklist independently of Hyperbridge. Any automated solver that fills orders based on output-side profitability without deeply vetting every input token is exposed.

### Recommendation
- In `_withdraw`, use a bounded low-level call per token transfer (mirroring the accepted MultiRewards fix) and skip/record failures instead of reverting the whole loop, so one bad token cannot block release of the others.
- Alternatively, require solvers/relayers to be able to redeem per-token rather than only via one atomic multi-token `WithdrawalRequest`, or let governance/the gateway mark a specific commitment's poisoned token as "forfeited" to unblock the rest.
- Consider an input-token allowlist enforced at `placeOrder` time so solvers don't need to individually vet arbitrary attacker-supplied token addresses per order.

### Proof of Concept
1. Attacker deploys `PoisonToken` (ERC-20 with an owner-controlled `pause()`/blacklist that makes `transfer` revert for a chosen address).
2. Attacker calls `placeOrder` with `inputs = [{USDC, 1000e6}, {PoisonToken, 1}]`, requesting a normal output on a destination chain.
3. A solver fills the order cross-chain via `fillOrder`, transferring the requested output tokens to the beneficiary and triggering dispatch of `RedeemEscrow{tokens: order.inputs}` back to the source gateway.
4. Attacker calls `PoisonToken.pause()` (or blacklists the gateway/solver) before the `RedeemEscrow` message is delivered.
5. On delivery, `onAccept` → `_withdraw` reverts on the `PoisonToken.safeTransfer` call inside the loop; the entire transaction, including the `USDC` payout to the solver, reverts.
6. Every retry of the same message delivery fails identically — the solver's `USDC` (and the escrowed `PoisonToken`) are permanently stuck in `_orders[commitment]`, while the solver has already paid the output assets on the destination chain.

### Citations

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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L139-155)
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
        DispatchPost memory request = DispatchPost({
            dest: order.source,
            to: abi.encodePacked(_instance(order.source)),
            body: body,
            timeout: 0,
            fee: options.relayerFee,
            payer: msg.sender
        });
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L241-267)
```text
        if (order.deadline >= _blockNumber()) {
            if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();
        }

        _filled[commitment] = address(uint160(uint256(order.user)));

        bytes memory body = bytes.concat(
            bytes1(uint8(RequestKind.RefundEscrow)),
            abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}))
        );

        DispatchPost memory request = DispatchPost({
            dest: order.source,
            to: abi.encodePacked(_instance(order.source)),
            body: body,
            timeout: 0,
            fee: options.relayerFee,
            payer: msg.sender
        });

        address hostAddr = host();
        if (msg.value > 0) {
            IDispatcher(hostAddr).dispatch{value: msg.value}(request);
        } else {
            dispatchWithFeeToken(request);
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
