### Title
Unchecked ERC20 return value in `IntentGatewayV2.withdraw()` allows silent fund loss on escrow release — ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The Tron fork of `IntentGatewayV2` releases escrowed tokens using a raw low-level `.call` with the `IERC20.transfer` selector, but only checks that the call did not revert (`success`) — it never inspects the returned boolean. Non-standard ERC20 tokens that signal failure by returning `false` (instead of reverting) will be treated as a successful transfer. The escrow accounting is updated and the intent is marked filled/refunded even though the beneficiary received nothing, permanently locking the tokens inside the contract while the protocol believes settlement succeeded.

### Finding Description
`withdraw()` is the internal function invoked from `onAccept()` when a `RedeemEscrow` or `RefundEscrow` request is authenticated from the counterpart chain: [1](#0-0) 

Inside `withdraw()`, ERC20 token transfers are performed like this: [2](#0-1) 

`token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount))` only reverts (`success == false`) if the token contract itself reverts or the call target has no code. Many ERC20 implementations (and especially non-standard/deflationary/paused tokens) return `false` on failure without reverting. Because the returned `bytes` payload is discarded (`(bool success,) = ...`), such a `false` return is indistinguishable from a genuine success. The function then unconditionally does:

```
_orders[body.commitment][token] -= amount;
```

and, before the loop even runs, has already set:

```
_filled[body.commitment] = beneficiary;
```

So escrow state is finalized (`EscrowReleased`/`EscrowRefunded` emitted) as if funds were delivered, while the tokens remain stuck in the `IntentGatewayV2` contract balance. The same unchecked pattern is repeated for transaction-fee redemption and for `SweepDust`: [3](#0-2) [4](#0-3) 

This is a direct structural analog to the reported bug class: the code assumes a single, uniform ERC20 transfer semantic (`call succeeds ⇒ transfer succeeded`) across all tokens, but a subset of real-world tokens break that assumption — exactly like `Blast_Adapter.relayTokens` assuming `depositERC20To` exists for every token when DAI required a different call path. Here the mismatch isn't a revert (which would just be a visible DoS) — it's a silent `false` return that the code fails to check, producing a wrong-outcome / fund-loss condition instead of a wrong-function-call/revert.

Other parts of the codebase avoid this class of bug entirely by using OpenZeppelin's `SafeERC20.safeTransfer`/`safeTransferFrom`, which decode and require the returned boolean (or accept no-return tokens), e.g. `WrappedHyperFungibleToken.onAccept` and `onPostRequestTimeout`: [5](#0-4) 

and the non-Tron `IntentGatewayV2` (`evm/src/apps/*`) which routes token transfers through `IntentsBase.sol` helpers using `safeTransfer`/`safeTransferFrom` rather than raw `.call`. The Tron deployment is the one execution path that regressed to the unchecked pattern, mirroring how only the Blast adapter (and not the standard bridge path) carried the broken assumption.

### Impact Explanation
This directly matches the bounty's "stealing or loss of funds" and "false proof/state acceptance" impact classes: escrow accounting (`_orders[...]`) is decremented and settlement is finalized (`_filled` set, event emitted) even though the beneficiary never received the tokens. The tokens become permanently locked in the `IntentGatewayV2` contract with no way to re-trigger withdrawal for that commitment (since `_orders[commitment][token]` is already decremented and `_filled` already set, a retry would revert with `UnknownOrder` or be treated as already filled). This is unauthorized-execution-adjacent fund loss that requires no malicious relayer, prover, or admin — it is triggered purely by which ERC20 token is used in the order, an attacker-controllable input via `order.inputs`/`order.output.assets` token selection in intent creation.

### Likelihood Explanation
Likelihood is realistic wherever the Tron `IntentGatewayV2` is deployed with any ERC20 that does not strictly revert-on-failure (a well-documented and common class of tokens, e.g. tokens with blacklists, pausability, or legacy non-reverting implementations). An order creator (unprivileged) simply needs to select such a token as an input/output asset for the corresponding solver-fill or refund path to hit `withdraw()`'s unchecked transfer, causing that specific settlement to silently fail while being recorded as completed.

### Recommendation
Replace the raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` pattern in `withdraw()` (both the token loop and the fee-redemption block) and in the `SweepDust` handler with OpenZeppelin's `SafeERC20.safeTransfer`, consistent with the rest of the codebase (`IntentsBase.sol`, `WrappedHyperFungibleToken.sol`). This ensures a `false` return value causes a revert rather than being silently treated as a successful transfer, keeping escrow state changes atomic with actual token delivery.

### Proof of Concept
1. Deploy (or reuse) a non-standard ERC20 on the Tron-fork chain whose `transfer()` returns `false` on failure instead of reverting (e.g., simulate a paused/blacklisted recipient scenario).
2. Create and escrow an intent order using this token as an input/output asset via the normal `IntentGatewayV2` order flow.
3. Have hyperbridge deliver an authenticated `RedeemEscrow`/`RefundEscrow` message that reaches `onAccept` → `withdraw()`, where the beneficiary address is one for which `transfer()` returns `false` (e.g., blacklisted).
4. Observe: `token.call(...)` returns `success == true` (call didn't revert) even though the encoded return data is `false`; `_orders[commitment][token] -= amount` executes, `_filled[commitment]` is set, and `EscrowReleased`/`EscrowRefunded` is emitted — while the beneficiary's token balance is unchanged and the tokens remain stuck in the `IntentGatewayV2` contract, unrecoverable through the normal withdrawal path.

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L661-671)
```text
                if (token == address(0)) {
                    (bool sent,) = req.beneficiary.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, req.beneficiary, amount));
                    if (!success) revert TransferFailed();
                }
                unchecked {
                    ++i;
                }
                emit DustSwept(token, amount, req.beneficiary);
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L693-705)
```text
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L707-714)
```text
        // redeem tx fees
        uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
        if (fees > 0) {
            address feeToken = IDispatcher(host()).feeToken();
            (bool success,) = feeToken.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, fees));
            if (!success) revert TransferFailed();
            delete _orders[body.commitment][TRANSACTION_FEES];
        }
```

**File:** sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol (L316-324)
```text
            IWETH(_underlying).withdraw(message.amount);
            (bool sent,) = beneficiary.call{value: message.amount}("");
            if (!sent) {
                IWETH(_underlying).deposit{value: message.amount}();
                IERC20(_underlying).safeTransfer(beneficiary, message.amount);
            }
        } else {
            IERC20(_underlying).safeTransfer(beneficiary, message.amount);
        }
```
