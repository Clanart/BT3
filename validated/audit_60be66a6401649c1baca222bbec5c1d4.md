### Title
Permanent lock of escrowed intent funds via reverting beneficiary in `withdraw()` - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The external report's core broken invariant is: a protocol pushes funds directly to a caller-controlled address instead of using a pull-based withdrawal pattern, and a single revert from that recipient (a contract that intentionally rejects the transfer) can permanently and irreversibly brick fund release, with no separate mechanism for the rightful owner to reclaim the value. Hyperbridge's intent-settlement escrow release path (`IntentGatewayV2.sol` / `ExtrinsicIntents.sol`) reproduces exactly this pattern for cross-chain order fills and refunds.

### Finding Description
When an order is filled or cancelled, the counterparty chain dispatches a `RedeemEscrow` or `RefundEscrow` POST request back to the chain holding the escrow. That request is delivered via `onAccept`, which calls the internal `withdraw()` function: [1](#0-0) 

`withdraw()` marks the order as filled and then pushes native ETH or ERC20 tokens directly to `beneficiary` (`order.user`, taken verbatim from the order struct that the order creator fully controls): [2](#0-1) 

If `beneficiary` is a smart contract that reverts on receiving ETH (or, for ERC20 outputs, a token whose `transfer` reverts, e.g. blacklist-style tokens), the `if (!sent) revert InsufficientNativeToken();` / `if (!success) revert TransferFailed();` check causes `withdraw()`, and therefore the entire `onAccept` call, to revert atomically. Because `_filled[body.commitment] = beneficiary;` and the escrow decrement `_orders[body.commitment][token] -= amount;` happen in the same call as the transfer, the revert rolls back the whole delivery — but it also means the incoming POST request delivered by the relayer never succeeds, so the escrow can never be released through this path. There is no alternate, pull-based function (`user => token => amount` balance mapping the user can withdraw independently) as recommended by the original report; the only release path is this single push-based `withdraw()`.

This mirrors the reported attacker primitive precisely: the attacker (order creator) controls `order.user`/`beneficiary` and can set it to a contract engineered to reject the ETH/token transfer, deliberately and permanently preventing settlement of that specific escrow — a self-inflicted-looking but externally triggerable "mass exit"-style lock at the level of individual intents, since there is no admin-independent recovery function for a specific stuck commitment, matching the report's core finding that "there is no way to reverse this... it is better to let users withdraw their ETH by themselves, in a separate function... increment a mapping like `user=>token=>amount`."

### Impact Explanation
Funds escrowed for a cross-chain order (`RedeemEscrow` fill payout or `RefundEscrow` cancellation refund) become permanently unrecoverable once the beneficiary is a maliciously reverting contract, because the only release code path is the direct push transfer inside `withdraw()`. This is a direct "loss/lock of funds" impact matching the bounty's accepted impact class (loss of funds via logic attack in escrow settlement), without requiring any relayer, prover, or admin compromise — only a normal order creation by an unprivileged user.

### Likelihood Explanation
High. Any user creating an order (`ExtrinsicIntents.sol` / `IntentGatewayV2.sol`) fully controls the `user`/`beneficiary` field encoded into the order, and can trivially deploy a contract with a reverting `receive()`/`fallback()` (or use a blacklisted ERC20 recipient) before dispatching. No special timing, race condition, or privileged role is required — the same permissionless entrypoint (`onAccept` via `handlePostRequests`) that normally settles orders is unconditionally exposed to this griefing pattern.

### Recommendation
Replace the direct push transfer in `withdraw()` with a pull-based accounting pattern: on `RedeemEscrow`/`RefundEscrow` delivery, credit an internal `beneficiary => token => amount` balance instead of calling `.call{value:}`/`transfer` directly, and expose a separate `claim()` function that the beneficiary (or anyone, on their behalf) can call to pull funds. This prevents a single reverting recipient from blocking settlement of the underlying ISMP message and from permanently locking the associated escrow.

### Proof of Concept
1. Attacker deploys `Reverter`, a contract with `receive() external payable { revert(); }`.
2. Attacker creates a cross-chain order via `ExtrinsicIntents`/`IntentGatewayV2` with `order.user = address(Reverter)` and a native-ETH output.
3. Order is legitimately filled or cancelled on the counterparty chain, triggering a `RedeemEscrow`/`RefundEscrow` POST request back to the escrow chain.
4. A relayer calls `handlePostRequests`, which calls `onAccept` → `withdraw()`.
5. `beneficiary.call{value: amount}("")` fails because `Reverter` rejects ETH, `withdraw()` reverts, and the entire request delivery reverts.
6. Every retry attempt (any relayer resubmitting the same proof) hits the identical revert; the escrow entry in `_orders[commitment][token]` is never decremented and can never be released, because `withdraw()` has no fallback pull-based claim path. [2](#0-1)

### Citations

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
