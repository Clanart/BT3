### Title
Fee-on-Transfer Token Accounting Discrepancy in `init_transfer` Leads to Bridge Undercollateralization - (File: `starknet/src/omni_bridge.cairo`)

### Summary
The StarkNet `init_transfer` function calls `transfer_from` with a caller-supplied `amount` and then emits an `InitTransfer` event recording that same `amount`. For fee-on-transfer ERC-20 tokens, the bridge contract receives fewer tokens than `amount`, but the event (which is proven to NEAR to finalize the cross-chain transfer) records the full `amount`. NEAR then releases or mints the full `amount`, leaving the StarkNet bridge undercollateralized by the transfer fee on every such bridging operation.

### Finding Description

In `starknet/src/omni_bridge.cairo`, the `init_transfer` function accepts a caller-supplied `amount` parameter, performs a `transfer_from` for that `amount`, and immediately emits an event containing that same `amount`:

```cairo
fn init_transfer(
    ref self: ContractState,
    token_address: ContractAddress,
    amount: u128,
    ...
) {
    ...
    let success = IERC20Dispatcher { contract_address: token_address }
        .transfer_from(caller, get_contract_address(), amount.into());
    assert(success, 'ERR_TRANSFER_FROM_FAILED');
    ...
    self.emit(Event::InitTransfer(InitTransfer {
        ...
        amount,   // ← parameter value, not actual received balance
        ...
    }))
}
``` [1](#0-0) [2](#0-1) 

The function never measures the actual token balance received (e.g., via a pre/post `balanceOf` check). For a standard ERC-20 token this is harmless, but for a fee-on-transfer token the bridge receives `amount - transfer_fee` while the emitted event records `amount`.

The emitted event is the source of truth consumed by the NEAR prover. On the NEAR side, `fin_transfer` processes a `ProverResult::InitTransfer(InitTransferMessage { amount, ... })` and releases or mints exactly `amount` tokens to the recipient: [3](#0-2) 

This creates a permanent per-transfer deficit of `transfer_fee` tokens in the StarkNet bridge vault, which compounds across every bridging operation using such a token.

### Impact Explanation

**High — Balance/accounting corruption that breaks bridge collateralization.**

Each `init_transfer` call with a fee-on-transfer token causes the StarkNet bridge to hold fewer tokens than the NEAR side believes it has locked. After enough transfers, the StarkNet bridge cannot honor withdrawals back from NEAR: when users bridge back, the `fin_transfer` on StarkNet attempts to `transfer` the full recorded amount but the vault is short by the accumulated fees, causing the transfer to fail or drain reserves belonging to other users. This directly breaks bridge collateralization and can result in permanent freezing of user funds.

### Likelihood Explanation

**Medium.** The bridge's token registration process does not appear to explicitly exclude fee-on-transfer tokens. Any token that passes the `transfer_from` success check (which fee-on-transfer tokens do — they return `true`) can be used. An attacker or even an ordinary user bridging a fee-on-transfer token triggers this silently. The deficit is small per transaction but accumulates monotonically and is irrecoverable without manual intervention.

### Recommendation

Before emitting the `InitTransfer` event, measure the actual balance received by performing a `balanceOf` check before and after the `transfer_from` call, and use the difference as the canonical `amount` in the event:

```cairo
let balance_before = IERC20Dispatcher { contract_address: token_address }
    .balance_of(get_contract_address());
let success = IERC20Dispatcher { contract_address: token_address }
    .transfer_from(caller, get_contract_address(), amount.into());
assert(success, 'ERR_TRANSFER_FROM_FAILED');
let balance_after = IERC20Dispatcher { contract_address: token_address }
    .balance_of(get_contract_address());
let actual_amount: u128 = (balance_after - balance_before).try_into().unwrap();
// use actual_amount in the emitted event and fee validation
```

Additionally, validate that `actual_amount >= fee` after the measurement to prevent zero-value or negative-net transfers.

### Proof of Concept

1. A fee-on-transfer token (e.g., 1% fee on every transfer) is registered in the Omni Bridge on StarkNet.
2. Attacker (or any user) calls `init_transfer` with `amount = 1000`, `fee = 0`.
3. StarkNet bridge receives `990` tokens (1% fee deducted by the token contract), but emits `InitTransfer { amount: 1000, ... }`.
4. The event is proven to NEAR via the prover. NEAR's `fin_transfer` reads `amount = 1000` from the proof and mints/releases `1000` tokens to the recipient.
5. The StarkNet bridge vault is now short by `10` tokens.
6. Repeated over 100 such transfers: vault is short `1000` tokens.
7. When any user bridges back `1000` tokens from NEAR to StarkNet, the `fin_transfer` on StarkNet calls `transfer(recipient, 1000)` but the vault only holds `900` (assuming all 100 prior transfers used this token), causing the transfer to fail — permanently freezing those user funds. [4](#0-3)

### Citations

**File:** starknet/src/omni_bridge.cairo (L281-331)
```text
        fn init_transfer(
            ref self: ContractState,
            token_address: ContractAddress,
            amount: u128,
            fee: u128,
            native_fee: u128,
            recipient: ByteArray,
            message: ByteArray,
        ) {
            assert(!_is_paused(@self, PAUSE_INIT_TRANSFER), 'ERR_INIT_TRANSFER_PAUSED');

            assert(amount > 0, 'ERR_ZERO_AMOUNT');
            assert(fee < amount, 'ERR_INVALID_FEE');

            let origin_nonce = self.current_origin_nonce.read() + 1;
            self.current_origin_nonce.write(origin_nonce);

            let caller = get_caller_address();

            if self.is_bridge_token(token_address) {
                IBridgeTokenDispatcher { contract_address: token_address }
                    .burn(caller, amount.into());
            } else {
                let success = IERC20Dispatcher { contract_address: token_address }
                    .transfer_from(caller, get_contract_address(), amount.into());
                assert(success, 'ERR_TRANSFER_FROM_FAILED');
            }

            if native_fee > 0 {
                let native_token = self.strk_token_address.read();
                let success = IERC20Dispatcher { contract_address: native_token }
                    .transfer_from(caller, get_contract_address(), native_fee.into());
                assert(success, 'ERR_FEE_TRANSFER_FAILED');
            }

            self
                .emit(
                    Event::InitTransfer(
                        InitTransfer {
                            sender: caller,
                            token_address,
                            origin_nonce,
                            amount,
                            fee,
                            native_fee,
                            recipient,
                            message,
                        },
                    ),
                )
        }
```

**File:** near/omni-bridge/src/lib.rs (L1867-1865)
```rust

```
