Confirmed: both `RedeemEscrow` (solver payout) and `RefundEscrow` (user cancellation refund) route through `onAccept` → `withdraw()` at [1](#0-0) , and both `WithdrawalRequest.tokens` are populated directly from `order.inputs` (unfiltered) at [2](#0-1) . This is enough to finalize the analog.

### Title
Zero-amount input token entry in an Intent order permanently bricks escrow settlement on Tron `IntentGatewayV2.withdraw` - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The Kairos bug is about an auction price hitting `0`, after which a token that reverts on zero-value transfers makes the NFT unclaimable forever. The structural analog in Hyperbridge's Tron `IntentGatewayV2` is a legitimately-zero escrow ledger entry for one input token in a multi-token order, which makes `withdraw()` unconditionally revert for the *entire* order — permanently blocking both the solver's escrow redemption and the user's cancellation refund, with no alternate code path to recover the (non-zero) escrowed funds of the other tokens in the same order.

### Finding Description
`placeOrder` only requires `order.inputs.length != 0` [3](#0-2) . The per-token non-zero check `if (order.inputs[i].amount == 0) revert InvalidInput();` only exists in the "no predispatch" branch [4](#0-3) . When an order uses `predispatch.call`/`predispatch.assets`, the escrow-crediting loop pulls tokens back from the `CallDispatcher` and sets `_orders[commitment][token] += reducedInputs[i].amount` for every entry in `order.inputs`, with **no check that `order.inputs[i].amount` (or the resulting `reducedInputs[i].amount`) is non-zero** [5](#0-4) . A user can therefore place a valid order whose `order.inputs` array contains one legitimate, funded token (e.g. token A) and one deliberately zero-amount phantom entry (token B), giving `_orders[commitment][B] == 0`.

Both settlement paths forward the full, unfiltered `order.inputs` array as `WithdrawalRequest.tokens`:
- Cross-chain fill → `RedeemEscrow` to the source chain, `tokens: order.inputs` (mainline `ExtrinsicIntents.sol` `_fillCrossChain`).
- Destination cancellation → `RefundEscrow` to the source chain, `tokens: order.inputs` [2](#0-1) .

On the source chain, `onAccept` routes both message kinds straight into `withdraw()` [1](#0-0) , which iterates every token in `body.tokens` and does:

```solidity
if (_orders[body.commitment][token] == 0) revert UnknownOrder();
``` [6](#0-5) 

There is no `if (amount == 0) continue;` guard before this check (unlike the mainline EVM `IntentsBase.sol::_withdraw`, which explicitly does `if (amount == 0) continue;` before touching escrow [7](#0-6) ). Because token B's escrow ledger entry is `0` by construction, `withdraw()` reverts with `UnknownOrder` on **every** call for that commitment — for the fill/redeem path *and* the refund/cancel path — since both dispatch the exact same fixed `order.inputs` list, which was baked into the order commitment at placement time and cannot be altered afterward.

### Impact Explanation
The revert is unconditional and commitment-bound: neither a relayer, solver, nor the order's own user can construct a different `WithdrawalRequest` for that commitment, because `commitment = keccak256(abi.encode(order))` is fixed and `_filled`/`_orders` are keyed by it. Once a solver fills such an order cross-chain (delivering real output assets to the user on the destination chain), the solver's `RedeemEscrow` claim against the source-chain escrow will always revert, permanently losing the solver's compensation. Symmetrically, if the order is never filled, the user's own `RefundEscrow` cancellation path also always reverts, permanently locking the user's legitimately escrowed token A along with the phantom zero balance of token B. This is a genuine, permanent loss/lock of funds reachable by an ordinary user through a public entrypoint (`placeOrder`), with no reliance on a malicious relayer, prover, or governance actor.

### Likelihood Explanation
High: constructing such an order requires only calling `placeOrder` with `predispatch.call.length > 0 && predispatch.assets.length > 0` (any trivial call qualifies) and an `order.inputs` array containing an extra token entry with `amount = 0`. No special token behavior (e.g., revert-on-zero-transfer ERC20) is even required, unlike the original Kairos report — the contract's own `UnknownOrder` check on the escrow ledger fires unconditionally regardless of the underlying token's transfer semantics, making this strictly easier to trigger than the source bug.

### Recommendation
In `withdraw()` (and any other escrow-release loop in the Tron `IntentGatewayV2`), skip zero-amount token entries before evaluating escrow presence, mirroring the mainline `IntentsBase.sol::_withdraw` pattern:
```solidity
uint256 amount = body.tokens[i].amount;
if (amount == 0) continue;
if (_orders[body.commitment][token] == 0) revert UnknownOrder();
```
Additionally, close the validation gap at the source: require `order.inputs[i].amount > 0` for every entry regardless of whether the predispatch branch is taken, so no order can ever be committed with a zero-amount input token.

### Proof of Concept
1. User calls `placeOrder(order, graffiti)` where `order.predispatch.call` is a trivial no-op call and `order.predispatch.assets` funds a small predispatch swap of token A; `order.inputs = [{token: A, amount: 1000}, {token: B, amount: 0}]`.
2. `placeOrder` computes `reducedInputs` (A: reduced amount, B: `0`) and sets `_orders[commitment][A] = reducedAmountA`, `_orders[commitment][B] = 0` [5](#0-4) .
3. A solver fills the order cross-chain and dispatches `RedeemEscrow` with `tokens: order.inputs` (= `[A, B]`).
4. On the source chain, `onAccept` calls `withdraw(body, false)`; the loop reaches token B, evaluates `_orders[commitment][B] == 0`, and reverts `UnknownOrder()` — the whole transaction (including token A's release to the solver) reverts [6](#0-5) .
5. Since `order.inputs` (and thus `WithdrawalRequest.tokens`) is fixed by the commitment, every future attempt (redeem or refund) for this commitment replays the same failing token list and reverts identically — token A's escrow is permanently stuck.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L332-334)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable {
        // Validate that order has inputs
        if (order.inputs.length == 0) revert InvalidInput();
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L410-440)
```text
            // Transfer tokens from call dispatcher back to IntentGateway
            Call[] memory transferCalls = new Call[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 requiredAmount = order.inputs[i].amount;
                uint256 balance;

                if (token == address(0)) {
                    balance = address(dispatcher).balance;
                    if (balance < requiredAmount) revert InsufficientNativeToken();
                    transferCalls[i] = Call({to: address(this), value: balance, data: ""});
                } else {
                    balance = IERC20(token).balanceOf(dispatcher);
                    if (balance < requiredAmount) revert InvalidInput();
                    transferCalls[i] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                }

                uint256 dust = balance - requiredAmount;
                if (dust > 0) emit DustCollected(token, dust);

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L444-454)
```text
        } else {
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                if (token == address(0)) {
                    // native token
                    if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
                    msgValue -= order.inputs[i].amount;
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L588-591)
```text
            bytes memory body = bytes.concat(
                bytes1(uint8(RequestKind.RefundEscrow)),
                abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}))
            );
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-695)
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
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L394-403)
```text
        uint256 len = body.tokens.length;
        for (uint256 i; i < len; i++) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (amount == 0) continue;

            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
```
