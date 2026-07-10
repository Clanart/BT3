### Title
Native ETH `finTransfer` Permanently Locks Funds When Recipient Contract Cannot Receive ETH — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.sol`'s `finTransfer` uses a single-path low-level ETH push when finalizing native ETH transfers (`tokenAddress == address(0)`). If the recipient is a contract that reverts on receiving ETH, the finalization always reverts. Because the recipient address is fixed inside the MPC-signed payload and there is no fallback delivery path or on-chain cancel/refund mechanism, the ETH is permanently locked in the bridge contract and the corresponding tokens on NEAR are permanently locked in the NEAR bridge's `pending_transfers` map.

---

### Finding Description

In `OmniBridge.sol`, `finTransfer` first marks the destination nonce as consumed, then attempts to push ETH to the recipient:

```solidity
completedTransfers[payload.destinationNonce] = true;
// ...
if (payload.tokenAddress == address(0)) {
    (bool success, ) = payload.recipient.call{value: payload.amount}("");
    if (!success) revert FailedToSendEther();
}
``` [1](#0-0) 

If `payload.recipient` is a contract without a `receive()` / `fallback()` function, or one that explicitly reverts, the low-level call returns `success = false`. The `revert FailedToSendEther()` rolls back the entire transaction, including the `completedTransfers` write, so the nonce is **not** consumed. However, the recipient address is embedded in the MPC-signed `TransferMessagePayload` and cannot be changed without a new MPC signature. No alternative delivery path (pull-payment mapping, admin rescue, or re-routing) exists in the contract. [2](#0-1) 

On the NEAR side, when a user initiates a NEAR → EVM transfer, the tokens are stored in `pending_transfers` inside the NEAR bridge contract: [3](#0-2) 

There is no cancel or refund function visible in the NEAR bridge that would allow a user to reclaim tokens from a stuck `pending_transfers` entry. The `token_lock.rs` module only exposes `lock_tokens_if_needed` / `unlock_tokens_if_needed` for internal accounting; there is no user-callable escape hatch. [4](#0-3) 

---

### Impact Explanation

**Permanent freezing / irrecoverable lock of user funds** — matching the allowed Critical impact class.

- The ETH deposited into `OmniBridge.sol` during the original EVM → NEAR leg is permanently locked in the bridge contract.
- The wrapped tokens on NEAR are permanently locked in `pending_transfers` with no user-accessible refund path.
- Neither the user nor any permissionless actor can recover the funds; only an admin upgrade could rescue them.

---

### Likelihood Explanation

**Moderate.** Any user who specifies a contract address as the EVM recipient that does not implement ETH receipt (e.g., a multisig, a DAO treasury, a DeFi protocol contract, or any contract compiled without a `receive()` function) triggers this condition. This is a realistic and common mistake. The bridge UI cannot prevent it because the recipient is a free-form string signed by MPC.

---

### Recommendation

Replace the ETH push with a **pull-payment pattern**. If the direct send fails, credit the amount to a per-recipient claimable balance rather than reverting:

```solidity
mapping(address => uint256) public claimableEth;

// Inside finTransfer, replace the ETH branch:
if (payload.tokenAddress == address(0)) {
    (bool success, ) = payload.recipient.call{value: payload.amount}("");
    if (!success) {
        // Mark nonce used, but park ETH for recipient to pull
        claimableEth[payload.recipient] += payload.amount;
    }
}

function claimEth() external {
    uint256 amount = claimableEth[msg.sender];
    require(amount > 0, "Nothing to claim");
    claimableEth[msg.sender] = 0;
    (bool success, ) = msg.sender.call{value: amount}("");
    require(success, "Transfer failed");
}
```

This mirrors the mitigation recommended in H-07: provide an alternative settlement path when the primary delivery mechanism fails, so funds are never permanently stranded.

---

### Proof of Concept

1. Alice bridges 1 ETH from EVM to NEAR: calls `initTransfer(address(0), 1e18, ...)` with `msg.value = 1e18`. ETH is held in `OmniBridge.sol`. [5](#0-4) 
2. NEAR bridge mints 1 wrapped-ETH for Alice on NEAR.
3. Alice initiates a return transfer via `ft_transfer_call` on NEAR, specifying `recipient = <ContractThatRevertsOnETH>` on EVM. The NEAR bridge stores the transfer in `pending_transfers`. [6](#0-5) 
4. Relayer calls `sign_transfer` on NEAR; MPC signs a `TransferMessagePayload` with `tokenAddress = address(0)` and `recipient = <ContractThatRevertsOnETH>`.
5. Relayer calls `finTransfer` on EVM. The nonce is marked used, then `<ContractThatRevertsOnETH>.call{value: 1e18}("")` returns `false`. `revert FailedToSendEther()` fires, rolling back the nonce mark. [1](#0-0) 
6. Every subsequent retry of `finTransfer` with the same signed payload produces the same revert.
7. The 1 ETH is permanently locked in `OmniBridge.sol`. The wrapped-ETH entry in NEAR's `pending_transfers` is permanently locked with no refund path. Alice's funds are irrecoverable without an admin contract upgrade.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L283-322)
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
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L373-413)
```text
    function initTransfer(
        address tokenAddress,
        uint128 amount,
        uint128 fee,
        uint128 nativeFee,
        string calldata recipient,
        string calldata message
    ) external payable whenNotPaused(PAUSED_INIT_TRANSFER) {
        currentOriginNonce += 1;
        if (fee >= amount) {
            revert InvalidFee();
        }

        uint256 extensionValue;
        if (tokenAddress == address(0)) {
            if (fee != 0) {
                revert InvalidFee();
            }
            extensionValue = msg.value - amount - nativeFee;
        } else {
            extensionValue = msg.value - nativeFee;
            if (customMinters[tokenAddress] != address(0)) {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    customMinters[tokenAddress],
                    amount
                );
                ICustomMinter(customMinters[tokenAddress]).burn(
                    tokenAddress,
                    amount
                );
            } else if (isBridgeToken[tokenAddress]) {
                BridgeToken(tokenAddress).burn(msg.sender, amount);
            } else {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    address(this),
                    amount
                );
            }
        }
```

**File:** near/omni-bridge/src/lib.rs (L222-223)
```rust
    pub pending_transfers: LookupMap<TransferId, TransferMessageStorage>,
    pub finalised_transfers: LookupSet<TransferId>,
```

**File:** near/omni-bridge/src/lib.rs (L523-619)
```rust
    fn init_transfer(
        &mut self,
        sender_id: AccountId,
        signer_id: AccountId,
        token_id: AccountId,
        amount: U128,
        init_transfer_msg: InitTransferMsg,
    ) -> PromiseOrPromiseIndexOrValue<U128> {
        require!(
            init_transfer_msg.recipient.get_chain() != ChainKind::Near,
            BridgeError::InvalidRecipientChain.as_ref()
        );

        self.current_origin_nonce += 1;
        let destination_nonce =
            self.get_next_destination_nonce(init_transfer_msg.get_destination_chain());

        let transfer_message = TransferMessage {
            origin_nonce: self.current_origin_nonce,
            token: OmniAddress::Near(token_id),
            amount,
            recipient: init_transfer_msg.recipient,
            fee: Fee {
                fee: init_transfer_msg.fee,
                native_fee: init_transfer_msg.native_token_fee,
            },
            sender: OmniAddress::Near(sender_id),
            msg: init_transfer_msg.msg.map(String::from).unwrap_or_default(),
            destination_nonce,
            origin_transfer_id: None,
        };
        require!(
            transfer_message.fee.fee < transfer_message.amount,
            BridgeError::InvalidFee.as_ref()
        );

        let required_storage_balance =
            self.required_balance_for_init_transfer_message(transfer_message.clone());

        let message_storage_account_id = transfer_message
            .calculate_storage_account_id(init_transfer_msg.external_id.map(String::from));

        // Choose storage payer or whether to yield execution until storage is available
        if self
            .try_to_transfer_balance_from_message_account(
                &message_storage_account_id,
                NearToken::from_yoctonear(init_transfer_msg.native_token_fee.0),
                &signer_id,
                required_storage_balance,
            )
            .is_ok()
            || (self.has_storage_balance(
                &signer_id,
                required_storage_balance.saturating_add(NearToken::from_yoctonear(
                    init_transfer_msg.native_token_fee.0,
                )),
            ) && (init_transfer_msg.native_token_fee.0 == 0
                || !self.acl_has_role(Role::NativeFeeRestricted.into(), signer_id.clone())))
        {
            PromiseOrPromiseIndexOrValue::Value(
                self.init_transfer_internal(transfer_message, signer_id),
            )
        } else {
            let promise_index = env::promise_yield_create(
                "init_transfer_resume",
                json!({
                    "transfer_message": transfer_message,
                    "message_storage_account_id": message_storage_account_id,
                    "storage_owner": signer_id,
                })
                .to_string()
                .as_bytes(),
                INIT_TRANSFER_RESUME_GAS,
                GasWeight(0),
                PROMISE_REGISTER_ID,
            );

            let yield_id: CryptoHash = env::read_register(PROMISE_REGISTER_ID)
                .near_expect(BridgeError::ReadPromiseRegister)
                .try_into()
                .near_expect(BridgeError::ReadPromiseYieldId);

            let required_storage_balance = self.add_promise(&message_storage_account_id, &yield_id);

            self.update_storage_balance(
                env::current_account_id(),
                required_storage_balance,
                NearToken::from_yoctonear(0),
            );

            env::log_str(&format!(
                "Yield init transfer until storage is available at {message_storage_account_id}"
            ));

            PromiseOrPromiseIndexOrValue::PromiseIndex(promise_index)
        }
    }
```

**File:** near/omni-bridge/src/token_lock.rs (L96-120)
```rust
    pub(crate) fn lock_tokens_if_needed(
        &mut self,
        chain_kind: ChainKind,
        token_id: &AccountId,
        amount: u128,
    ) -> LockAction {
        if self.get_token_origin_chain(token_id) == chain_kind || amount == 0 {
            return LockAction::Unchanged;
        }

        self.lock_tokens(chain_kind, token_id, amount)
    }

    pub(crate) fn unlock_tokens_if_needed(
        &mut self,
        chain_kind: ChainKind,
        token_id: &AccountId,
        amount: u128,
    ) -> LockAction {
        if self.get_token_origin_chain(token_id) == chain_kind || amount == 0 {
            return LockAction::Unchanged;
        }

        self.unlock_tokens(chain_kind, token_id, amount)
    }
```
