### Title
Missing Recipient Zero/Empty Validation in `initTransfer` Permanently Locks User Assets - (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

### Summary
`OmniBridge.initTransfer` and `OmniBridge.initTransfer1155` accept a user-supplied `recipient` string with no validation that it is non-empty or parseable as a valid `OmniAddress`. A user who calls either function with `recipient = ""` (or any unparseable string) will have their tokens irreversibly locked or burned on the EVM side, while the NEAR bridge will be unable to finalize the transfer, resulting in permanent loss of funds. The same root cause exists in `starknet/src/omni_bridge.cairo`'s `init_transfer`.

### Finding Description
In `OmniBridge.initTransfer` the `recipient` parameter is a raw `string calldata` that is accepted and emitted without any non-empty or format check:

```solidity
function initTransfer(
    address tokenAddress,
    uint128 amount,
    uint128 fee,
    uint128 nativeFee,
    string calldata recipient,   // ← no require(bytes(recipient).length > 0)
    string calldata message
) external payable whenNotPaused(PAUSED_INIT_TRANSFER) {
``` [1](#0-0) 

Tokens are locked or burned before the recipient is ever validated:

- For native ERC-20 tokens: `IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount)` — tokens are now held by the bridge.
- For bridge tokens: `BridgeToken(tokenAddress).burn(msg.sender, amount)` — tokens are destroyed. [2](#0-1) 

The same pattern applies to `initTransfer1155`: [3](#0-2) 

After the lock/burn, the `InitTransfer` event is emitted with the empty recipient string. The NEAR bridge's `fin_transfer_callback` attempts to decode the proof and parse the recipient as an `OmniAddress`. An empty or malformed string cannot be parsed into any `OmniAddress` variant, so the proof is rejected and the transfer can never be finalized on NEAR. [4](#0-3) 

There is no refund, cancel, or recovery function in `OmniBridge.sol` for a stuck `initTransfer`. The contract is UUPS-upgradeable, so admin intervention via an upgrade is theoretically possible, but there is no on-chain self-service recovery path for the user.

The identical root cause exists in StarkNet's `init_transfer`, which also accepts `recipient: ByteArray` without any non-empty assertion before burning/locking tokens: [5](#0-4) 

The Solana program has the same gap and it is explicitly acknowledged as a known low-severity issue in `solana/SECURITY.md` line 17:

> **No validation of `recipient` string in `InitTransferPayload`** — An invalid recipient causes the transfer to fail on the NEAR side after tokens are locked/burned on Solana. Manual intervention would be needed. [6](#0-5) 

The EVM and StarkNet instances carry the same root cause but are **not** acknowledged in any SECURITY.md.

### Impact Explanation
A user who calls `initTransfer("")` (or any unparseable recipient) on the EVM bridge:
1. Has their ERC-20 tokens permanently locked inside `OmniBridge` (or bridge tokens permanently burned).
2. Receives no refund because no on-chain recovery path exists.
3. Cannot re-submit the transfer because the nonce has already been incremented and the lock/burn is irreversible.

This matches the Critical allowed impact: **Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.**

### Likelihood Explanation
The scenario is directly analogous to the referenced report (a caller passing a zero/null destination by mistake). Realistic triggers include:
- A frontend bug that submits an empty recipient string.
- A programmatic integrator (bot, relayer, DApp) that fails to populate the recipient field before calling `initTransfer`.
- A user who manually constructs a transaction and omits the recipient.

No privileged role is required; any token holder can trigger this by calling `initTransfer` directly.

### Recommendation
Add a non-empty recipient check at the top of `initTransfer` and `initTransfer1155` in `OmniBridge.sol`, before any token movement occurs:

```solidity
require(bytes(recipient).length > 0, "ERR_EMPTY_RECIPIENT");
```

Add the equivalent assertion in StarkNet's `init_transfer` before the burn/lock:

```cairo
assert(recipient.len() > 0, 'ERR_EMPTY_RECIPIENT');
```

Optionally, validate that the recipient string is parseable as a known `OmniAddress` format (e.g., starts with a recognized chain prefix such as `"near:"`, `"eth:"`, `"sol:"`) to catch malformed addresses beyond the empty-string case.

### Proof of Concept
1. Deploy `OmniBridge` on a local EVM fork.
2. Approve the bridge to spend 1000 USDC.
3. Call:
   ```solidity
   bridge.initTransfer(
       USDC_ADDRESS,
       1000,
       0,       // fee
       0,       // nativeFee
       "",      // recipient — empty string
       ""       // message
   );
   ```
4. Observe: `safeTransferFrom` succeeds, 1000 USDC is now held by the bridge, `currentOriginNonce` is incremented, and the `InitTransfer` event is emitted with an empty recipient.
5. On the NEAR side, attempt to call `fin_transfer` with a proof of this event. The prover will fail to parse `""` as an `OmniAddress`, the callback panics, and the transfer is never finalized.
6. The 1000 USDC remains permanently locked in `OmniBridge` with no on-chain recovery path. [7](#0-6) [8](#0-7)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L373-437)
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

        initTransferExtension(
            msg.sender,
            tokenAddress,
            currentOriginNonce,
            amount,
            fee,
            nativeFee,
            recipient,
            message,
            extensionValue
        );

        emit BridgeTypes.InitTransfer(
            msg.sender,
            tokenAddress,
            currentOriginNonce,
            amount,
            fee,
            nativeFee,
            recipient,
            message
        );
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L439-490)
```text
    function initTransfer1155(
        address tokenAddress,
        uint256 tokenId,
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

        address deterministicToken = deriveDeterministicAddress(
            tokenAddress,
            tokenId
        );

        IERC1155(tokenAddress).safeTransferFrom(
            msg.sender,
            address(this),
            tokenId,
            amount,
            ""
        );

        uint256 extensionValue = msg.value - nativeFee;

        initTransferExtension(
            msg.sender,
            deterministicToken,
            currentOriginNonce,
            amount,
            fee,
            nativeFee,
            recipient,
            message,
            extensionValue
        );

        emit BridgeTypes.InitTransfer(
            msg.sender,
            deterministicToken,
            currentOriginNonce,
            amount,
            fee,
            nativeFee,
            recipient,
            message
        );
    }
```

**File:** near/omni-bridge/src/lib.rs (L700-746)
```rust
    pub fn fin_transfer_callback(
        &mut self,
        #[serializer(borsh)] storage_deposit_actions: &Vec<StorageDepositAction>,
        #[serializer(borsh)] predecessor_account_id: AccountId,
    ) -> PromiseOrValue<Nonce> {
        let Ok(ProverResult::InitTransfer(init_transfer)) = Self::decode_prover_result(0) else {
            env::panic_str(BridgeError::InvalidProofMessage.to_string().as_str())
        };
        require!(
            self.factories
                .get(&init_transfer.emitter_address.get_chain())
                == Some(init_transfer.emitter_address),
            BridgeError::UnknownFactory.as_ref()
        );

        let decimals = self
            .token_decimals
            .get(&init_transfer.token)
            .near_expect(BridgeError::TokenDecimalsNotFound);

        let destination_nonce =
            self.get_next_destination_nonce(init_transfer.recipient.get_chain());
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
        }
    }
```

**File:** starknet/src/omni_bridge.cairo (L281-307)
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
```

**File:** solana/SECURITY.md (L17-17)
```markdown
- **No validation of `recipient` string in `InitTransferPayload`** — An invalid recipient causes the transfer to fail on the NEAR side after tokens are locked/burned on Solana. Manual intervention would be needed.
```
