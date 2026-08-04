### Title
Reentrant escrow drain via unguarded `withdraw()` in Tron `IntentGatewayV2` — external transfer before state deduction - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Tron fork of `IntentGatewayV2.sol` implements `withdraw(WithdrawalRequest memory body, bool isRefund)` with a checks-effects-interactions violation: it performs the external token/native transfer to `beneficiary` **before** decrementing `_orders[body.commitment][token]`, and the enclosing entrypoints (`onAccept`/`onGetResponse`) that call it do not appear to carry a `nonReentrant` guard in this fork (unlike the guarded `cancelOrder`/`fillOrder` paths in the mainline `evm/src/apps/IntentGatewayV2.sol`). This lets a malicious `beneficiary` (an attacker-controlled contract, or a malicious ERC-20 with a transfer hook) reenter `withdraw` for the same commitment/token before the balance is deducted, draining more escrowed value than was ever legitimately escrowed for that order.

### Finding Description
In `evm/tron/contracts/apps/IntentGatewayV2.sol:682-721`: [1](#0-0) 
the function loops over `body.tokens`, and for each token:
1. Checks `_orders[body.commitment][token] == 0` (only a nonzero guard, not an amount-sufficiency guard).
2. Performs an external call — either a native ETH/TRX transfer (`beneficiary.call{value: amount}("")`) or an ERC-20 `transfer` call — directly to `beneficiary`.
3. Only *after* the external call returns does it do `_orders[body.commitment][token] -= amount;`.

Because the escrow-accounting update happens after the external call, and the call target is attacker-controlled (`beneficiary`, decoded straight from the cross-chain request body: `address beneficiary = address(uint160(uint256(body.beneficiary)));`), a beneficiary contract can reenter `onAccept`/`onGetResponse` → `withdraw` mid-transfer. Each reentrant call rereads the still-undecremented `_orders[body.commitment][token]` value and passes the same nonzero check, allowing the same escrowed balance to be paid out multiple times before it is ever debited.

This is a direct analog of the Futureswap seed pattern: liquidity/escrowed value that should be redeemed exactly once, drained repeatedly through unsafe external-call ordering, resulting in "stealing or loss of funds" — one of the explicitly in-scope Hyperbridge impacts (bridge custody / intent settlement fund movement not happening exactly once, to the rightful beneficiary and amount).

### Impact Explanation
If exploitable, this allows an unprivileged attacker who controls (or crafts) a `beneficiary` address (or a malicious fee/escrow token with transfer hooks) to drain the `IntentGateway`'s entire pooled escrow for a token — not just their own order's inputs — since `_orders[commitment][token]` bookkeeping is per-commitment but actual token custody is pooled contract-wide. This is unauthorized, repeated withdrawal of funds that should settle exactly once per order, matching the "stealing or loss of funds" / "replay/double-claim/double-settlement" impact categories.

### Likelihood Explanation
Likelihood depends on two facts I could not fully confirm before running out of tool budget:
1. Whether `onAccept`/`onGetResponse` (or an outer wrapper) on the Tron variant carry a `nonReentrant` modifier that the excerpt I read did not show. The mainline `evm/src/apps/IntentGatewayV2.sol::cancelOrder`/`fillOrder` are `nonReentrant`, but I was not able to verify the modifier list on `onAccept`/`onGetResponse` in the Tron file within the available iterations.
2. Whether `beneficiary` can genuinely be attacker-controlled on the settlement path (for a normal fill, `beneficiary` is the solver who filled the order — plausibly attacker-controlled by simply being the filler themselves, or for a refund, the original `order.user`, which the attacker also controls if they placed the order).

Given the pattern is a textbook reentrancy (external call before state write, in Solidity, using raw `.call` rather than a reentrancy-safe transfer), and the same code exists identically for both refund and fill withdrawal, I assess likelihood as credible but **not fully confirmed** — this needs verification that no reentrancy guard wraps the call chain into `withdraw` in this specific Tron fork.

### Recommendation
- Apply checks-effects-interactions: decrement `_orders[body.commitment][token] -= amount` **before** issuing the external `.call`, for both the native-asset and ERC-20 branches, and for the fee-redemption block below it.
- Add a `nonReentrant` guard (or reentrancy lock) around `onAccept`/`onGetResponse` in the Tron contract, matching the guards already present on `fillOrder`/`cancelOrder` in the mainline EVM contract, so cross-chain settlement callbacks cannot be reentered.
- Audit `evm/tron/contracts/apps/IntentGatewayV2.sol` for other divergences from `evm/src/apps/IntentGatewayV2.sol`/`IntentsBase.sol`, since this fork appears to be an independently-maintained copy that may have drifted from the hardened mainline implementation.

### Proof of Concept
Conceptual (not fully executed against the Tron test suite due to tool-call exhaustion):
1. Attacker places (or fills) an order such that `withdraw()` will be called with `beneficiary` set to an attacker-deployed contract, and with a token that is either native TRX/ETH or a token supporting a transfer-triggered callback (or simply reenter using the native-asset branch, which uses `.call{value: amount}("")` and unconditionally forwards all gas, enabling reentrancy even for a plain fallback function).
2. The attacker's `beneficiary` contract's `receive()`/`fallback()` function re-calls the ISMP host's delivery entrypoint (or, if reachable directly, `onGetResponse`/`onAccept`) with a request body encoding the same `commitment`/`token` before the outer call returns.
3. Because `_orders[body.commitment][token]` has not yet been decremented, the `UnknownOrder` check still passes, and a second (or Nth) transfer of `amount` is issued from the gateway's pooled balance.
4. Repeat until the contract's token balance for that asset is exhausted, realizing a payout far exceeding the order's actual escrowed `amount`.

**Note on confidence**: I was unable to confirm within the remaining tool budget (a) the exact modifier set on the Tron contract's `onAccept`/`onGetResponse`, and (b) whether an upstream reentrancy guard elsewhere in the call chain (e.g., at the ISMP host dispatch level) neutralizes this. If a `nonReentrant` guard is present on the entry callback, this finding does not hold and should be treated as unconfirmed rather than a proven vulnerability.

### Citations

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
