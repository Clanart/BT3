### Title
Unregistered Token in StarkNet `init_transfer` Causes Permanent Fund Lock via `TokenDecimalsNotFound` Panic in NEAR `fin_transfer_callback` — (`starknet/src/omni_bridge.cairo`, `near/omni-bridge/src/lib.rs`)

---

### Summary

The StarkNet bridge's `init_transfer` accepts any `token_address` without verifying it is registered in NEAR's `token_decimals` or `token_address_to_id` mappings. When a user transfers an unregistered token, their funds are immediately locked/burned on StarkNet. The NEAR `fin_transfer_callback` then panics with `TokenDecimalsNotFound` because the token has no entry in `token_decimals`. No refund or cancel mechanism exists on StarkNet, making the lock permanent.

---

### Finding Description

**StarkNet `init_transfer` — no token registration guard**

`starknet/src/omni_bridge.cairo:281-331` accepts any `token_address`:

```cairo
fn init_transfer(ref self: ContractState, token_address: ContractAddress, ...) {
    assert(!_is_paused(@self, PAUSE_INIT_TRANSFER), 'ERR_INIT_TRANSFER_PAUSED');
    assert(amount > 0, 'ERR_ZERO_AMOUNT');
    assert(fee < amount, 'ERR_INVALID_FEE');
    // No check: is token_address registered on NEAR?
    if self.is_bridge_token(token_address) {
        IBridgeTokenDispatcher { contract_address: token_address }.burn(caller, amount.into());
    } else {
        let success = IERC20Dispatcher { contract_address: token_address }
            .transfer_from(caller, get_contract_address(), amount.into());
        assert(success, 'ERR_TRANSFER_FROM_FAILED');
    }
    self.emit(Event::InitTransfer(InitTransfer { sender: caller, token_address, ... }))
}
``` [1](#0-0) 

`is_bridge_token` only returns true for tokens deployed via `deploy_token` (i.e., tokens with a `starknet_to_near_token` mapping entry). An arbitrary ERC20 takes the `transfer_from` path and is locked in the bridge contract. [2](#0-1) 

**`parse_init_transfer` — no registry validation**

The NEAR-side event parser blindly wraps the raw felt as `OmniAddress::Strk(H256(keys[2]))` with no lookup against any token registry: [3](#0-2) 

**NEAR `fin_transfer_callback` — panics on unregistered token**

After the factory check passes (the event was emitted by the legitimate registered StarkNet bridge), the callback immediately panics:

```rust
let decimals = self
    .token_decimals
    .get(&init_transfer.token)
    .near_expect(BridgeError::TokenDecimalsNotFound);
``` [4](#0-3) 

The factory check at lines 708–713 passes because the proof is from the legitimate StarkNet bridge contract — it does not validate the token itself. [5](#0-4) 

**No refund mechanism on StarkNet**

The entire `starknet/src/omni_bridge.cairo` contains no `cancel_transfer`, `refund`, or `rescue` function. Once `init_transfer` succeeds on StarkNet, the tokens are irrecoverably locked in the bridge contract.



---

### Impact Explanation

When `fin_transfer_callback` panics, NEAR rolls back its own state changes (no `TransferMessage` is stored, no tokens are minted/released). However, the StarkNet state is already committed — the user's tokens are locked in the bridge contract or burned. There is no cross-chain rollback. The result is **permanent, irrecoverable loss** of the user's tokens on StarkNet.

This matches: **Critical — Permanent freezing, irrecoverable lock of user funds in bridge flows.**

---

### Likelihood Explanation

- Any user can call StarkNet `init_transfer` with any ERC20 token address — no privileged role required.
- A user could accidentally use a token that has not yet been deployed/bound on NEAR (e.g., a newly listed token, a token on a different chain variant, or a typo in the address).
- The StarkNet contract provides no feedback that the token is unregistered on NEAR before funds are committed.
- A relayer submitting the proof to NEAR is required to trigger the panic, but even without a relayer, the funds remain locked on StarkNet with no finalization path.

---

### Recommendation

1. **StarkNet-side guard**: In `init_transfer`, require that `token_address` is either a bridge token (`is_bridge_token`) or has a known NEAR mapping (`starknet_to_near_token.read(token_address).len() > 0`). Reject transfers for unregistered tokens before locking funds.
2. **NEAR-side graceful error**: Replace the `near_expect` panic in `fin_transfer_callback` with a graceful error path that records the failed transfer and allows a future refund proof to be submitted.
3. **Refund mechanism on StarkNet**: Add a `cancel_transfer` or `refund_transfer` function on StarkNet that can be triggered by a NEAR-side proof of failed finalization, returning locked tokens to the original sender.

---

### Proof of Concept

1. Deploy any ERC20 token on StarkNet that is **not** registered in NEAR's `token_decimals`.
2. Approve the StarkNet bridge to spend tokens.
3. Call `init_transfer(unregistered_token, amount, fee, 0, "victim.near", "")` — succeeds, tokens locked in bridge.
4. Submit the resulting `InitTransfer` event proof to NEAR `fin_transfer`.
5. `fin_transfer_callback` panics: `token_decimals.get(&OmniAddress::Strk(unregistered_token))` returns `None` → `near_expect(BridgeError::TokenDecimalsNotFound)` panics.
6. NEAR state is rolled back; StarkNet state is not. Tokens are permanently locked.
7. No `cancel_transfer` or refund path exists on StarkNet.

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

**File:** starknet/src/omni_bridge.cairo (L378-380)
```text
        fn is_bridge_token(self: @ContractState, token_address: ContractAddress) -> bool {
            self.starknet_to_near_token.read(token_address).len() > 0
        }
```

**File:** near/omni-types/src/starknet/events.rs (L49-50)
```rust
    let sender = OmniAddress::Strk(H256(keys[1]));
    let token = OmniAddress::Strk(H256(keys[2]));
```

**File:** near/omni-bridge/src/lib.rs (L708-713)
```rust
        require!(
            self.factories
                .get(&init_transfer.emitter_address.get_chain())
                == Some(init_transfer.emitter_address),
            BridgeError::UnknownFactory.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L715-718)
```rust
        let decimals = self
            .token_decimals
            .get(&init_transfer.token)
            .near_expect(BridgeError::TokenDecimalsNotFound);
```
