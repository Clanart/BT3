### Title
Missing Recipient Validation in `initTransfer` Enables Irrecoverable Fund Lock With No Recovery Path - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`, `starknet/src/omni_bridge.cairo`, `near/omni-bridge/src/lib.rs`)

---

### Summary

The `initTransfer` entry points on EVM and StarkNet do not validate the `recipient` parameter before burning or locking user tokens. If a user supplies an empty or unparseable recipient string, tokens are irrecoverably destroyed or locked on the source chain while the NEAR side cannot complete the transfer. Unlike the Connext delegate pattern (which provides an on-chain recovery address), Omni Bridge has no on-chain mechanism to rescue funds from such stuck transfers. The Solana variant of this issue is already acknowledged in `solana/SECURITY.md`; the EVM and StarkNet variants are not documented.

---

### Finding Description

**Vulnerability class:** Missing validation of a parameter whose absence causes permanent fund loss with no on-chain recovery path — the direct analog of the "delegate not enforced" class in the reference report.

**EVM — `OmniBridge.sol::initTransfer` (lines 373–437)**

`recipient` is a raw `string calldata`. No length or content check is performed before tokens are burned or locked:

```solidity
function initTransfer(
    address tokenAddress,
    uint128 amount,
    uint128 fee,
    uint128 nativeFee,
    string calldata recipient,   // ← never validated
    string calldata message
) external payable whenNotPaused(PAUSED_INIT_TRANSFER) {
    ...
    BridgeToken(tokenAddress).burn(msg.sender, amount);   // tokens gone
    ...
    emit BridgeTypes.InitTransfer(..., recipient, ...);   // empty string emitted
}
```

The same gap exists in `initTransfer1155` (lines 439–490).

**StarkNet — `omni_bridge.cairo::init_transfer` (lines 281–330)**

`recipient` is a `ByteArray`. Only `amount > 0` and `fee < amount` are checked; the recipient is never validated before tokens are burned or locked:

```cairo
fn init_transfer(
    ref self: ContractState,
    token_address: ContractAddress,
    amount: u128,
    fee: u128,
    native_fee: u128,
    recipient: ByteArray,   // ← never validated
    message: ByteArray,
) {
    assert(amount > 0, 'ERR_ZERO_AMOUNT');
    assert(fee < amount, 'ERR_INVALID_FEE');
    // tokens burned here, then event emitted with unvalidated recipient
```

**NEAR — `lib.rs::init_transfer` (lines 523–619)**

The only guard is that the recipient chain must not be `Near`. A zero-value address on any other chain (e.g., `eth:0x0000…0000`) passes unchecked:

```rust
require!(
    init_transfer_msg.recipient.get_chain() != ChainKind::Near,
    BridgeError::InvalidRecipientChain.as_ref()
);
// No is_zero() check; zero EVM/Sol/Strk address accepted
```

`OmniAddress::is_zero()` exists in `near/omni-types/src/lib.rs` (lines 299–313) but is never called here.

**No on-chain recovery path exists.** There is no `cancel_transfer`, `refund_transfer`, or delegate-equivalent function anywhere in the NEAR bridge contract. Once tokens are burned/locked and the event is emitted, the only recourse is off-chain manual intervention by the protocol team — which is not guaranteed and is not encoded in the protocol.

---

### Impact Explanation

**Critical — Permanent freezing / irrecoverable lock of user funds.**

1. User calls `initTransfer` on EVM (or StarkNet) with an empty `recipient` string (or a zero-address recipient on NEAR).
2. Tokens are burned (bridge tokens) or locked (native tokens) on the source chain — irreversibly within that transaction.
3. The `InitTransfer` event is emitted with the invalid recipient.
4. The NEAR bridge reads the event. Parsing an empty string as an `OmniAddress` fails; the transfer cannot be finalized on the destination chain.
5. No on-chain function exists to refund the sender or re-route the transfer. Funds are permanently frozen.

The `solana/SECURITY.md` itself confirms the consequence: *"An invalid recipient causes the transfer to fail on the NEAR side after tokens are locked/burned on Solana. Manual intervention would be needed."* The EVM and StarkNet paths carry the identical risk without even that acknowledgment.

---

### Likelihood Explanation

**Low-to-Medium.** The trigger is an unprivileged bridge user supplying a bad recipient — plausible via:
- UI/SDK bug that passes an empty string on error.
- Direct contract call by a developer testing or scripting a transfer.
- Copy-paste error in an integration.
- Intentional griefing of one's own funds (e.g., to test the protocol).

No privileged access, key compromise, or external dependency failure is required. The call is fully permissionless.

---

### Recommendation

1. **Validate `recipient` is non-empty before any token movement** in `initTransfer` / `initTransfer1155` on EVM and `init_transfer` on StarkNet:
   ```solidity
   if (bytes(recipient).length == 0) revert InvalidRecipient();
   ```
   ```cairo
   assert(recipient.len() > 0, 'ERR_EMPTY_RECIPIENT');
   ```

2. **Validate non-zero address on NEAR** by calling `recipient.is_zero()` after the chain-kind check in `init_transfer`.

3. **Add an on-chain recovery path** (analogous to the Connext delegate): allow the original `sender` to cancel a pending transfer and reclaim tokens if the transfer has not been signed/finalized within a timeout window. This is the structural fix that mirrors the external report's recommendation.

4. **Document the behavior** in `evm/SECURITY.md` and `starknet/SECURITY.md` as the Solana team did, at minimum.

---

### Proof of Concept

**EVM path (empty recipient → permanent burn):**

```solidity
// Attacker/user holds 1000 USDC bridged tokens on Ethereum
OmniBridge.initTransfer(
    usdcBridgeToken,
    1000e6,
    0,          // fee
    0,          // nativeFee
    "",         // ← empty recipient; no revert
    ""
);
// BridgeToken.burn(msg.sender, 1000e6) executes — tokens destroyed
// InitTransfer event emitted with recipient = ""
// NEAR side: OmniAddress::from_str("") → parse error → transfer never finalized
// 1000 USDC permanently lost; no cancel/refund function exists
```

**StarkNet path (empty recipient → permanent lock):**

```cairo
dispatcher.init_transfer(token_address, 1000, 0, 0, "", "");
// transfer_from succeeds, tokens locked in bridge
// InitTransfer event emitted with recipient = ""
// NEAR side cannot parse recipient → transfer never finalized
```

**NEAR path (zero EVM address → delivery to burn address):**

```json
{ "recipient": "eth:0x0000000000000000000000000000000000000000", "fee": "0", "native_token_fee": "0" }
```
`is_zero()` is never called; the transfer proceeds, and tokens are minted to the EVM zero address on the destination chain — effectively burned. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** starknet/src/omni_bridge.cairo (L281-314)
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
```

**File:** near/omni-bridge/src/lib.rs (L531-534)
```rust
        require!(
            init_transfer_msg.recipient.get_chain() != ChainKind::Near,
            BridgeError::InvalidRecipientChain.as_ref()
        );
```

**File:** near/omni-types/src/lib.rs (L299-313)
```rust
    pub fn is_zero(&self) -> bool {
        match self {
            Self::Eth(address)
            | Self::Arb(address)
            | Self::Base(address)
            | Self::Bnb(address)
            | Self::Pol(address)
            | Self::HyperEvm(address)
            | Self::Abs(address) => address.is_zero(),
            Self::Near(address) => *address == ZERO_ACCOUNT_ID,
            Self::Sol(address) | Self::Fogo(address) => address.is_zero(),
            Self::Btc(address) | Self::Zcash(address) => address.is_empty(),
            Self::Strk(address) => address.is_zero(),
        }
    }
```

**File:** solana/SECURITY.md (L17-17)
```markdown
- **No validation of `recipient` string in `InitTransferPayload`** — An invalid recipient causes the transfer to fail on the NEAR side after tokens are locked/burned on Solana. Manual intervention would be needed.
```
