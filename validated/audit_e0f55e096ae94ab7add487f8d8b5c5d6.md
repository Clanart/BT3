## Title
Single malicious/reverting escrow token permanently locks all other escrowed funds and fees in IntentGatewayV2 withdrawal - (File: `evm/src/apps/intentsv2/IntentsBase.sol`, also `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`IntentsBase._withdraw` (and the equivalent `withdraw` in the Tron `IntentGatewayV2.sol`) releases every escrowed input token for an order in a single loop that transfers each token to one `beneficiary`, then (on finalize) also forwards accumulated transaction fees. Any single reverting/malicious ERC-20 among `order.inputs` causes the whole call to revert, permanently blocking release of *all* other (legitimate) escrowed tokens and fees for that order — the same class of bug as the C4 report's `withdrawTaxes()` finding.

### Finding Description
`_withdraw` iterates over `body.tokens` (built from `order.inputs`, which is fully attacker/user-controlled at order-creation time) and unconditionally calls `IERC20(token).safeTransfer(beneficiary, amount)` for each entry: [1](#0-0) 

If `safeTransfer` reverts for any one token in the array (e.g., the order creator includes a malicious ERC-20 as one of the multiple `order.inputs` that reverts on transfer, or a paused/blacklist-style token), the entire `_withdraw` transaction reverts. There is no per-token isolation and no try/catch, so a single bad token blocks release of every other token in the same `body.tokens` array as well as the subsequent fee transfer: [2](#0-1) 

The same pattern exists verbatim in the Tron variant's `withdraw` function, called from `onAccept` for `RequestKind.RedeemEscrow` / `RequestKind.RefundEscrow`: [3](#0-2) [4](#0-3) 

`order.inputs` are chosen entirely by the order creator when the order is placed (any ERC-20 address is accepted, with only a duplicate-token check), so an unprivileged user can construct an order whose input token list contains a legitimate, valuable token plus a malicious token designed to revert whenever the transfer recipient is a specific address (e.g. the solver who fills the order) or unconditionally (e.g. a paused/blacklisted stablecoin). Because `_withdraw`/`withdraw` is the only code path that releases escrow — invoked automatically via `onAccept`/`onGetResponse` once a fill or refund is proven cross-chain — there is no alternative mechanism to redeem the remaining, non-malicious tokens.

### Impact Explanation
This directly causes fund loss/lock in the bridge's intent-settlement custody path:
- A solver who has already paid out `order.output` assets to the user (honoring the fill) can be permanently denied their earned escrowed input tokens and accumulated transaction fees if even one input token in the order is malicious/reverting — the solver loses value that was already delivered.
- On the refund path, a legitimate user's other escrowed tokens become permanently unrecoverable if one input token in their own order later starts reverting (e.g., token gets paused by its issuer, or blacklists the specific beneficiary address).
- This matches the bounty's "stealing or loss of funds" and "logic attack" categories: escrowed value can be moved by nobody, indefinitely, due to normal user-supplied order data and no privileged/relayer/prover behavior is required.

### Likelihood Explanation
High likelihood: `order.inputs` accepts arbitrary token addresses with no allowlist enforcement visible in the escrow-crediting logic (`IntentGatewayV2.sol` lines 301-343), and creating a multi-input order with one bad-behaving token is a normal, permissionless action available to any user. The exploit needs no relayer, prover, or governance compromise — only crafting an order with a reverting ERC-20 as one of several inputs.

### Recommendation
Mirror the two mitigations from the referenced report:
1. Change escrow release to be per-token (accept a `poolId`/`commitment` + specific token index/subset) so a beneficiary can redeem all non-malicious tokens even if one token is broken.
2. Wrap each `safeTransfer`/native transfer in `_withdraw` (and the Tron `withdraw`) in a try/catch (or use low-level `call` and check success individually), skipping or re-crediting the escrow amount for the failing token instead of reverting the whole batch, and emit an event so the stuck balance can be recovered by governance sweep or a later retry rather than being permanently locked.

### Proof of Concept
1. Attacker (order creator) builds an `Order` with `order.inputs = [GOOD_TOKEN, EVIL_TOKEN]`, where `EVIL_TOKEN.transfer()` is coded to revert whenever `to == <solver address>` (or unconditionally, e.g. mimicking a pausable/blacklistable token).
2. Order is submitted normally; both tokens are escrowed via the standard credit-escrow flow in `IntentGatewayV2.sol` (lines 333-343 / Tron 381-440).
3. A solver fills the order, delivering `order.output` to the user off-chain/cross-chain as usual.
4. The fill is proven and `onAccept` dispatches `RequestKind.RedeemEscrow`, calling `withdraw(body, false)` / `_withdraw(body, false, true)` with `body.tokens = [GOOD_TOKEN, EVIL_TOKEN]` and `beneficiary = solver`.
5. The loop transfers `GOOD_TOKEN` successfully, then reverts on `EVIL_TOKEN.transfer(solver, amount)`.
6. The entire transaction reverts: `GOOD_TOKEN`'s escrow decrement is rolled back, the solver never receives `GOOD_TOKEN` or the transaction fees, and there is no other function to retry redemption of just `GOOD_TOKEN` — both tokens (and fees) remain stuck in the contract indefinitely. [5](#0-4)

### Citations

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
