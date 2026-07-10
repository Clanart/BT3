### Title
Permanent Freezing of Bridged Funds When EVM Recipient Is Token-Blacklisted — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`finTransfer` in `OmniBridge.sol` unconditionally pushes tokens to the hardcoded `payload.recipient`. If that address is blacklisted by the destination token (e.g., USDC, USDT), every finalization attempt reverts. Because the recipient is cryptographically bound in the MPC-signed payload and there is no cancel or redirect mechanism on the NEAR source chain, the user's locked/burned tokens become permanently irrecoverable.

---

### Finding Description

`finTransfer` marks the nonce used, verifies the MPC signature, then immediately pushes tokens to `payload.recipient`:

```solidity
// OmniBridge.sol line 287
completedTransfers[payload.destinationNonce] = true;
// ...
// line 351-354
IERC20(payload.tokenAddress).safeTransfer(
    payload.recipient,
    payload.amount
);
```

`safeTransfer` (OpenZeppelin) reverts on failure. Because the entire transaction reverts, `completedTransfers` is also rolled back — the nonce is never consumed. However, the `payload.recipient` is immutably encoded in the MPC-signed `borshEncoded` blob (line 298: `Borsh.encodeAddress(payload.recipient)`). No alternative recipient can be substituted without invalidating the signature. Every future call to `finTransfer` for this transfer will revert identically.

On the NEAR source chain, `init_transfer_internal` has already either burned the bridge tokens (`burn_tokens_if_needed`) or incremented `locked_tokens` for native tokens. There is no `cancel_transfer`, `refund_transfer`, or any other function that allows the sender to reclaim funds once the transfer message is committed and the MPC has signed it. The `fin_transfer_send_tokens_callback` refund path (lines 1702–1718) only handles NEAR-side `ft_transfer_call` failures, not EVM-side push failures.

The same push pattern exists in the StarkNet `fin_transfer` (line 250 sets the nonce, lines 260–262 push via `transfer`/`assert`), but the NEAR→EVM path is the primary affected flow because USDC/USDT are the dominant bridged stablecoins on EVM.

---

### Impact Explanation

**Critical — Permanent freezing / irrecoverable lock of user funds.**

A user who bridges USDC from NEAR to an EVM address that is subsequently (or already) USDC-blacklisted loses their funds permanently:
- Source-chain tokens are burned or locked with no refund path.
- Destination-chain finalization always reverts.
- No admin function can redirect the transfer to a different recipient without a new MPC signature over a different payload.

---

### Likelihood Explanation

**Low.** USDC/USDT blacklisting is rare but real (Circle has blacklisted hundreds of addresses). A user could also self-inflict this by bridging to a contract address that rejects ERC-20 transfers (e.g., a non-receiver contract), which is more common. The scenario requires no privileged access — any unprivileged bridge user is exposed.

---

### Recommendation

Adopt a pull-over-push pattern for EVM finalization:

1. Instead of transferring directly to `payload.recipient` inside `finTransfer`, credit the amount to a `claimable[recipient][token]` mapping and emit an event.
2. Add a separate `claimTransfer(address token)` function that lets the recipient pull their funds.
3. Alternatively, allow the original sender (proven via a new MPC-signed message) to redirect an unclaimed transfer to a different address.

---

### Proof of Concept

1. Alice holds 1000 USDC on NEAR and calls `init_transfer` targeting her EVM address `0xAlice`. NEAR bridge burns/locks her tokens and the MPC signs `TransferMessagePayload{recipient: 0xAlice, tokenAddress: USDC, amount: 1000}`.
2. Circle blacklists `0xAlice` (e.g., due to a sanctions hit).
3. Any relayer calls `OmniBridge.finTransfer(sig, payload)`:
   - Line 287: `completedTransfers[nonce] = true` (in memory, not yet committed).
   - Line 351: `IERC20(USDC).safeTransfer(0xAlice, 1000)` → USDC reverts with `Blacklisted`.
   - Entire transaction reverts; `completedTransfers[nonce]` is rolled back.
4. Step 3 can be repeated indefinitely — always reverts.
5. Alice's NEAR tokens are permanently gone (burned/locked). No `cancel_transfer` exists on NEAR. Funds are irrecoverable.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L283-355)
```text
        if (completedTransfers[payload.destinationNonce]) {
            revert NonceAlreadyUsed(payload.destinationNonce);
        }

        completedTransfers[payload.destinationNonce] = true;

        bytes memory borshEncoded = bytes.concat(
            bytes1(uint8(BridgeTypes.PayloadType.TransferMessage)),
            Borsh.encodeUint64(payload.destinationNonce),
            bytes1(payload.originChain),
            Borsh.encodeUint64(payload.originNonce),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.tokenAddress),
            Borsh.encodeUint128(payload.amount),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.recipient),
            bytes(payload.feeRecipient).length == 0 // None or Some(String) in rust
                ? bytes("\x00")
                : bytes.concat(
                    bytes("\x01"),
                    Borsh.encodeString(payload.feeRecipient)
                ),
            bytes(payload.message).length == 0
                ? bytes("")
                : Borsh.encodeBytes(payload.message)
        );
        bytes32 hashed = keccak256(borshEncoded);

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
        }

        MultiTokenInfo memory multiToken = multiTokens[payload.tokenAddress];

        if (payload.tokenAddress == address(0)) {
            // slither-disable-next-line arbitrary-send-eth
            (bool success, ) = payload.recipient.call{value: payload.amount}(
                ""
            );
            if (!success) revert FailedToSendEther();
        } else if (multiToken.tokenAddress != address(0)) {
            IERC1155(multiToken.tokenAddress).safeTransferFrom(
                address(this),
                payload.recipient,
                multiToken.tokenId,
                payload.amount,
                ""
            );
        } else if (customMinters[payload.tokenAddress] != address(0)) {
            ICustomMinter(customMinters[payload.tokenAddress]).mint(
                payload.tokenAddress,
                payload.recipient,
                payload.amount
            );
        } else if (isBridgeToken[payload.tokenAddress]) {
            if (payload.message.length == 0) {
                IBridgeToken(payload.tokenAddress).mint(
                    payload.recipient,
                    payload.amount
                );
            } else {
                IBridgeToken(payload.tokenAddress).mint(
                    payload.recipient,
                    payload.amount,
                    payload.message
                );
            }
        } else {
            IERC20(payload.tokenAddress).safeTransfer(
                payload.recipient,
                payload.amount
            );
        }
```

**File:** near/omni-bridge/src/lib.rs (L1700-1718)
```rust
        let token = self.get_token_id(&transfer_message.token);

        if Self::is_refund_required(is_ft_transfer_call) {
            self.burn_tokens_if_needed(
                token.clone(),
                U128(
                    transfer_message
                        .amount_without_fee()
                        .near_expect(BridgeError::InvalidFee),
                ),
            );

            self.revert_lock_actions(&lock_actions);

            self.remove_fin_transfer(&transfer_message.get_transfer_id(), storage_owner);

            env::log_str(
                &OmniBridgeEvent::FailedFinTransferEvent { transfer_message }.to_log_string(),
            );
```

**File:** near/omni-bridge/src/lib.rs (L1829-1865)
```rust
    fn init_transfer_internal(
        &mut self,
        transfer_message: TransferMessage,
        storage_owner: AccountId,
    ) -> U128 {
        let required_storage_balance = self
            .add_transfer_message(transfer_message.clone(), storage_owner.clone())
            .saturating_add(NearToken::from_yoctonear(transfer_message.fee.native_fee.0));

        if self
            .try_update_storage_balance(
                storage_owner,
                required_storage_balance,
                NearToken::from_yoctonear(0),
            )
            .is_err()
        {
            self.remove_transfer_message_without_refund(transfer_message.get_transfer_id());
            return transfer_message.amount;
        }

        if let OmniAddress::Near(token_id) = transfer_message.token.clone() {
            self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);

            self.lock_tokens_if_needed(
                transfer_message.get_destination_chain(),
                &token_id,
                transfer_message.amount.0,
            );
        } else {
            self.remove_transfer_message_without_refund(transfer_message.get_transfer_id());
            return transfer_message.amount;
        }

        env::log_str(&OmniBridgeEvent::InitTransferEvent { transfer_message }.to_log_string());
        U128(0)
    }
```

**File:** starknet/src/omni_bridge.cairo (L247-263)
```text
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
