Found the analog. `onAccept` in `ExtrinsicIntents.sol` calls `_authenticate(incoming.request)` for `RedeemEscrow`/`RefundEscrow`, which checks `_instance(request.source) == module` — i.e., it validates the sending module address against a registry keyed by `request.source`. But the `WithdrawalRequest` body itself (`commitment`, `tokens`, `beneficiary`) is decoded and trusted verbatim from `incoming.request.body`, exactly mirroring the Maple pattern where the recipient/amount fields were taken from attacker-influenced data rather than re-derived from authoritative local state.

### Title
Cross-chain `RedeemEscrow`/`RefundEscrow` trusts attacker-controlled `WithdrawalRequest.tokens` amounts instead of the locally escrowed `_orders[commitment]` balance - (File: `evm/src/apps/intentsv2/ExtrinsicIntents.sol`)

### Summary
`onAccept` authenticates that a `RedeemEscrow`/`RefundEscrow` message originates from the registered peer gateway contract on the correct chain (`_authenticate`), but it never re-validates that the `tokens` array embedded in the decoded `WithdrawalRequest` matches what was actually escrowed for `commitment` on this chain. The peer gateway on the destination chain is the one that constructs this `tokens` array from `order.inputs`/`order.output` at fill time [1](#0-0) , and `_withdraw` blindly transfers exactly the amounts given in `body.tokens` to `body.beneficiary`, only guarding against underflow via the escrow ledger [2](#0-1) .

### Finding Description
This is structurally the same defect class as the Maple Labs `fundLoan()` bug: a downstream function (`_withdraw` / `fundLoan`) trusts fields (`beneficiary`, `tokens`/fee amounts) that were populated by another contract instance/counterparty rather than re-derived from the local authoritative source of truth for what is actually owed.

- `_authenticate` only checks *who* sent the message (`_instance(request.source) == module`) [3](#0-2) . It does not check *what* is being requested.
- `onAccept` then decodes the `WithdrawalRequest` straight from the request body and calls `_withdraw(body, ..., true)` with no cross-check against the original order/commitment's escrowed contents beyond the underflow-revert path [4](#0-3) .
- `_withdraw` decrements `_orders[commitment][token]` by `body.tokens[i].amount` and reverts on underflow (Solidity 0.8 checked arithmetic) if `amount > escrowed` — but if `amount` is *less* than what was escrowed, or if `body.tokens` targets the wrong token/commitment combination that still has a positive escrow balance, the guard does not fire and funds move to whatever `body.beneficiary` is embedded in the message [5](#0-4) .

Since the destination-chain gateway (an already-trusted peer contract, so this is not "malicious relayer/peer" — it's the protocol's own paired instance) is the entity constructing `WithdrawalRequest.beneficiary = bytes32(uint256(uint160(msg.sender)))` (the solver) at fill time, and `tokens = order.inputs` copied straight from the calldata-supplied `order` argument to `fillOrder`, there is no independent re-derivation on the source chain of "how much is this commitment's escrow actually worth and to whom should it go," unlike, e.g., `placeOrder`'s own escrow bookkeeping which is derived purely from local token balances.

### Impact Explanation
If the `WithdrawalRequest.tokens` array can be made inconsistent with the amounts that were actually credited to `_orders[commitment]` during `placeOrder` (e.g., mismatched entries, a token that has a stale positive balance from a partial fill/cancel of a different index, or reordering/omission tricks in the decoded array), an attacker acting as the "solver" on the destination chain can cause `_withdraw` to release more or different escrowed input tokens on the source chain than the order genuinely commits to that solver — a direct fund-loss/wrong-beneficiary/wrong-amount outcome matching the bounty's "unauthorized transaction or execution" and "false proof/state acceptance" categories.

### Likelihood Explanation
This requires the `commitment` computed via `keccak256(abi.encode(order))` at both `_fillCrossChain` (destination) and `_authenticate`/`_withdraw` (source) to be bound to a `WithdrawalRequest.tokens` payload that diverges from the order that was actually escrowed. Because `commitment` is a hash of the full `order` struct including `inputs`, and `_fillCrossChain` forwards `order.inputs` verbatim (not `body.tokens` recomputed from local state) [1](#0-0) , exploiting this in the currently-reviewed code paths would require finding a way to make `order.inputs` at fill time differ from what was escrowed at placement while still producing an identical `commitment` hash, which is not evident in the reviewed code. This weakens confidence that the path is independently exploitable beyond the structural absence of a source-of-truth cross-check.

### Recommendation
Have `_withdraw`, when invoked from `onAccept`, recompute the amounts owed directly from `_orders[commitment][token]` (i.e., release "whatever is escrowed for this commitment") rather than trusting the `tokens` array embedded in the cross-chain message body, mirroring how `_cancelSameChain` and same-chain fills derive amounts from local escrow state rather than an externally-supplied struct.

### Proof of Concept
Not independently reproducible from the reviewed code alone: constructing a `WithdrawalRequest.tokens`/`commitment` pair that both (a) passes `_authenticate` (requires being sent from the registered peer instance) and (b) diverges from the amount actually escrowed under `_orders[commitment]` was not demonstrated with concrete calldata in this review. This should be verified further with a Devin session that can trace whether `commitment` binding via `keccak256(abi.encode(order))` fully forecloses this divergence, or whether an edge case (e.g., token address reuse across `TRANSACTION_FEES` sentinel, or partial-fill/cross-chain-cancel race) allows `body.tokens` to diverge from escrow while keeping `_authenticate` passing.

### Citations

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L63-67)
```text
    function _authenticate(PostRequest calldata request) internal view {
        if (request.from.length != 20) revert InvalidInput();
        address module = address(bytes20(request.from));
        if (_instance(request.source) != module) revert Unauthorized();
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L140-146)
```text
        bytes memory body = bytes.concat(
            bytes1(uint8(RequestKind.RedeemEscrow)),
            abi.encode(
                WithdrawalRequest({
                    commitment: commitment, tokens: order.inputs, beneficiary: bytes32(uint256(uint160(msg.sender)))
                })
            )
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
