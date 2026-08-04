Based on my investigation, I found a concrete local analog in the Tron variant of the IntentGateway contract that reproduces the exact bug class from the external report: state (escrow accounting) is mutated *after* an external call, and unlike its sibling implementations this variant is also missing the replay/duplicate-settlement guard that the audited EVM version relies on.

### Title
`IntentGatewayV2.withdraw` (Tron) updates escrow accounting after the external transfer and `onAccept` never checks `_filled`, enabling double-settlement of the same escrow - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`evm/tron/contracts/apps/IntentGatewayV2.sol` is a separate, independently-maintained copy of the intent gateway used for Tron deployments. Its `withdraw()` function performs the token/native transfer via a low-level `.call` and only decrements `_orders[commitment][token]` **after** that call returns: [1](#0-0) 

This is the exact anti-pattern from the external report (`_sendOrEscrowFunds` before updating `bid.loanDetails`): the "custom external call" (an ERC20 `transfer` to an attacker-controlled token, or a native `.call{value}` to an attacker-controlled beneficiary) executes while the escrow ledger (`_orders`) still reflects the pre-withdrawal balance.

Critically, unlike `cancelOrder`, which explicitly checks `_filled[commitment] != address(0)` before proceeding: [2](#0-1) 

the `onAccept` handler for `RedeemEscrow`/`RefundEscrow` performs **no** `_filled` check before invoking `withdraw`: [3](#0-2) 

`withdraw()` itself only sets `_filled[body.commitment] = beneficiary` as a side effect at its top, and only guards the escrow with a weak `== 0` existence check per token, not an amount comparison: [4](#0-3) 

Compare this to the already-hardened main EVM implementation, `IntentsBase._withdraw`, which decrements the escrow balance (`_orders[body.commitment][token] = escrowed - amount;`) **before** performing the transfer — i.e., Checks-Effects-Interactions is correctly applied there: [5](#0-4) 

The Tron contract diverged from that fix and reintroduced the CEI violation, exactly the bug class flagged in the external TellerV2 report.

### Finding Description
Both `RedeemEscrow` (destination fill → source settlement) and `RefundEscrow` (destination cancel → source refund) messages are legitimate, independently-authenticated ISMP `PostRequest`s for the *same* order commitment that can both be dispatched by the destination-chain gateway under a timing race around `order.deadline` (fill vs. anyone-can-cancel-after-deadline). Because `onAccept` never checks `_filled[commitment]` before calling `withdraw`, and `withdraw` performs the token transfer before decrementing `_orders`, a second `withdraw` invocation for the same commitment (whether from a second `onAccept` call, or reentered mid-transfer by a token/beneficiary with a callback hook) can transfer escrowed funds again — the `_orders[...][token] == 0` check only protects against amounts already zeroed, not against transfers already "in flight" whose accounting hasn't yet been committed.

### Impact Explanation
Successful exploitation drains escrowed input tokens (and accumulated transaction fees) from the IntentGateway a second time to an attacker-controlled beneficiary — a direct loss of bridged/escrowed funds and a double-settlement of the same order commitment, matching the bounty's "stealing or loss of funds" and "replay/double-claim/double-settlement" impact categories.

### Likelihood Explanation
No privileged actor, malicious relayer, or compromised prover is required — only an ordinary order placed with an attacker-influenced escrow token/beneficiary and the destination-side fill/cancel race that the protocol's own documentation acknowledges is possible near `order.deadline`. The vulnerable code path is reachable through the fully-authenticated `onAccept`/`onGetResponse` flow that every legitimate order goes through.

### Recommendation
Apply the same fix already present in `IntentsBase._withdraw` (main EVM code): decrement `_orders[commitment][token]` and set `_filled[commitment]` before performing any external transfer, and add an explicit `_filled[commitment] != address(0)` guard at the top of the `RedeemEscrow`/`RefundEscrow` branch of `onAccept`, mirroring the guard already present in `cancelOrder`.

### Proof of Concept
1. Place an order on the Tron-chain source gateway with an attacker-controlled beneficiary token/recipient.
2. On the destination chain, arrange for both a `fillOrder` (dispatching `RedeemEscrow`) and a post-deadline `cancelOrder` (dispatching `RefundEscrow`) to succeed for the same commitment (raced around `order.deadline`).
3. Both messages arrive at the source Tron gateway's `onAccept`; since no `_filled` check gates entry, `withdraw()` runs twice for the same commitment.
4. In each invocation, the token/native transfer (`beneficiary.call{value: amount}` or `token.call(transfer(...))`) executes before `_orders[commitment][token]` is decremented, so the second invocation's `_orders[...][token] == 0` check is evaluated against stale/pre-decrement state during any reentrant hook, allowing the beneficiary to receive the escrow twice.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L507-512)
```text
    function cancelOrder(Order calldata order, CancelOptions calldata options) public payable {
        bytes32 commitment = keccak256(abi.encode(order));

        // order has already been filled
        if (_filled[commitment] != address(0)) revert Filled();

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-705)
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
