### Title
Zero-Address Recipient Causes Permanent Fund Lock in `fin_transfer` — (`starknet/src/omni_bridge.cairo`)

---

### Summary

`OmniBridge::fin_transfer` does not validate that `payload.recipient` is non-zero before calling `BridgeToken::mint`. If NEAR signs a `fin_transfer` payload with `recipient = ContractAddress(0)` (possible via user-supplied zero address or address-derivation bug), the OZ ERC20 `mint` panics on every attempt, the transaction always reverts (rolling back the nonce mark), and the source-chain funds are permanently unclaimable with no on-chain recovery path.

---

### Finding Description

In `fin_transfer`, the nonce is marked consumed at line 250, then the signature is verified, then `mint` is called: [1](#0-0) 

```cairo
_set_transfer_finalised(ref self, payload.destination_nonce);   // line 250

_verify_borsh_signature(...);                                    // line 252-254

IBridgeTokenDispatcher { contract_address: payload.token_address }
    .mint(payload.recipient, payload.amount.into());             // line 257-258
```

`BridgeToken::mint` delegates directly to the OZ ERC20 internal `mint`: [2](#0-1) 

OpenZeppelin's StarkNet ERC20 `_mint` asserts `!recipient.is_zero()` and panics with `'ERC20: mint to the zero address'` when recipient is the zero address. In StarkNet, a panic rolls back **all** state changes in the transaction, including the `_set_transfer_finalised` write at line 250.

Consequence:
- The nonce is **never** durably consumed.
- Every retry of the same NEAR-signed payload panics identically.
- Source-chain funds (burned/locked on NEAR/EVM/Solana) are permanently unclaimable.
- No on-chain refund or rescue mechanism exists in the contract.

The NEAR bridge itself does not validate that a StarkNet destination recipient is non-zero before signing. The Solana side of the same codebase explicitly acknowledges the analogous gap: [3](#0-2) 

> "No validation of `recipient` string in `InitTransferPayload` — An invalid recipient causes the transfer to fail on the NEAR side after tokens are locked/burned on Solana."

The same pattern applies to StarkNet: NEAR's `fin_transfer_callback` constructs the `TransferMessage` from the prover result and passes the recipient through to `process_fin_transfer_to_other_chain` without zero-address validation: [4](#0-3) 

---

### Impact Explanation

A user who supplies `0x0` as their StarkNet recipient (or whose address is mis-derived as zero) will have their source-chain tokens burned/locked permanently. The StarkNet `fin_transfer` will revert on every attempt, the nonce will never be consumed, and no contract-level recovery path exists. This matches the **Critical — permanent irrecoverable lock** impact category.

---

### Likelihood Explanation

The path is reachable by any unprivileged user who initiates a cross-chain transfer and specifies the zero address as their StarkNet recipient. NEAR does not reject such a recipient, the MPC signs the resulting payload normally, and the relayer submits it to StarkNet. No privileged role or key compromise is required.

---

### Recommendation

Add a zero-address guard in `fin_transfer` before the mint/transfer dispatch:

```cairo
assert(!payload.recipient.is_zero(), 'ERR_ZERO_RECIPIENT');
``` [5](#0-4) 

Additionally, the NEAR contract should reject `OmniAddress::Starknet(0)` (and equivalent zero addresses for other chains) at `init_transfer` time, before funds are burned/locked.

---

### Proof of Concept

1. User calls `init_transfer` on NEAR specifying a StarkNet recipient of `0x0000...0000`.
2. NEAR burns/locks the tokens and stores the `TransferMessage` with `recipient = OmniAddress::Starknet(0)`.
3. MPC signs the Borsh-serialized payload (normal operation — no zero-address check).
4. Relayer calls `fin_transfer(sig, payload{recipient: 0, ...})` on StarkNet.
5. `_set_transfer_finalised` writes the nonce bit (line 250).
6. `_verify_borsh_signature` passes (signature is valid).
7. `IBridgeTokenDispatcher::mint(0, amount)` → OZ ERC20 panics with `'ERC20: mint to the zero address'`.
8. Entire transaction reverts; nonce bit is rolled back.
9. Steps 4–8 repeat identically on every retry.
10. Source-chain funds are permanently locked; no on-chain recovery path exists.

### Citations

**File:** starknet/src/omni_bridge.cairo (L242-263)
```text
        fn fin_transfer(
            ref self: ContractState, signature: Signature, payload: TransferMessagePayload,
        ) {
            assert(!_is_paused(@self, PAUSE_FIN_TRANSFER), 'ERR_FIN_TRANSFER_PAUSED');

            assert(
                !self.is_transfer_finalised(payload.destination_nonce), 'ERR_NONCE_ALREADY_USED',
            );
            _set_transfer_finalised(ref self, payload.destination_nonce);

            _verify_borsh_signature(
                ref self, @payload.to_borsh(self.omni_bridge_chain_id.read()), signature,
            );

            if self.is_bridge_token(payload.token_address) {
                IBridgeTokenDispatcher { contract_address: payload.token_address }
                    .mint(payload.recipient, payload.amount.into());
            } else {
                let success = IERC20Dispatcher { contract_address: payload.token_address }
                    .transfer(payload.recipient, payload.amount.into());
                assert(success, 'ERR_TRANSFER_FAILED');
            }
```

**File:** starknet/src/bridge_token.cairo (L74-77)
```text
    fn mint(ref self: ContractState, recipient: ContractAddress, amount: u256) {
        self.ownable.assert_only_owner();
        self.erc20.mint(recipient, amount);
    }
```

**File:** solana/SECURITY.md (L17-17)
```markdown
- **No validation of `recipient` string in `InitTransferPayload`** — An invalid recipient causes the transfer to fail on the NEAR side after tokens are locked/burned on Solana. Manual intervention would be needed.
```

**File:** near/omni-bridge/src/lib.rs (L722-744)
```rust
        let transfer_message = TransferMessage {
            origin_nonce: init_transfer.origin_nonce,
            token: init_transfer.token,
            amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
            recipient: init_transfer.recipient,
            fee: Self::denormalize_fee(&init_transfer.fee, decimals),
            sender: init_transfer.sender,
            msg: init_transfer.msg,
            destination_nonce,
            origin_transfer_id: None,
        };

        if let OmniAddress::Near(recipient) = transfer_message.recipient.clone() {
            self.process_fin_transfer_to_near(
                recipient,
                &predecessor_account_id,
                transfer_message,
                storage_deposit_actions,
            )
            .into()
        } else {
            self.process_fin_transfer_to_other_chain(predecessor_account_id, transfer_message);
            PromiseOrValue::Value(destination_nonce)
```
