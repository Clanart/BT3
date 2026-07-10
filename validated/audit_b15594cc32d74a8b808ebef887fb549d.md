### Title
Missing Recipient String Validation in `initTransfer` Enables Permanent Irrecoverable Fund Lock — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.initTransfer` accepts a raw `string calldata recipient` representing a cross-chain destination address but performs **no validation** on it. Any unprivileged user can call `initTransfer` with an empty or structurally invalid recipient string. Tokens are immediately locked or burned on the EVM side, yet the NEAR bridge will permanently fail to finalize the transfer because `OmniAddress::from_str` rejects the malformed string. No refund or cancellation path exists, making the loss irrecoverable.

---

### Finding Description

`OmniBridge.initTransfer` (lines 373–437) validates only that `fee >= amount` reverts. The `recipient` parameter — a free-form string that must encode a cross-chain address in `chain:address` format — is accepted without any length or format check:

```solidity
function initTransfer(
    address tokenAddress,
    uint128 amount,
    uint128 fee,
    uint128 nativeFee,
    string calldata recipient,   // ← no validation
    string calldata message
) external payable whenNotPaused(PAUSED_INIT_TRANSFER) {
    currentOriginNonce += 1;
    if (fee >= amount) {
        revert InvalidFee();
    }
    // tokens locked/burned here unconditionally
    ...
    emit BridgeTypes.InitTransfer(..., recipient, ...);
}
```

The same omission exists in `initTransfer1155` (lines 439–490).

The `recipient` string is serialised verbatim into the `InitTransfer` event. When a relayer later submits the proof to the NEAR bridge, `fin_transfer_callback` calls `Self::decode_prover_result(0)`, which internally invokes `OmniAddress::from_str` on the recipient string:

```rust
// near/omni-types/src/lib.rs  (OmniAddress::from_str)
fn from_str(input: &str) -> Result<Self, Self::Err> {
    let (chain, recipient) = input.split_once(':').unwrap_or(("eth", input));
    match chain {
        "eth" => Ok(Self::Eth(recipient.parse().map_err(stringify)?)),
        ...
        _ => Err(format!("Chain {chain} is not supported")),
    }
}
```

For an empty string `""`:
- `split_once(':')` returns `None`, so `chain = "eth"`, `recipient = ""`
- `"".parse::<H160>()` fails with `ERR_INVALID_HEX`

The existing test suite confirms this failure mode:

```rust
// near/omni-types/src/tests/lib_test.rs
(
    "invalid_format".to_string(),
    Err("ERR_INVALID_HEX".to_string()),
    "Should fail on missing chain prefix",
),
```

`fin_transfer_callback` then hits the mandatory panic branch:

```rust
// near/omni-bridge/src/lib.rs
let Ok(ProverResult::InitTransfer(init_transfer)) = Self::decode_prover_result(0) else {
    env::panic_str(BridgeError::InvalidProofMessage.to_string().as_str())
};
```

Every subsequent retry of `fin_transfer` with the same proof will also panic. The EVM contract has no `cancel`, `refund`, or `reclaim` function, so the locked/burned tokens are permanently unrecoverable.

---

### Impact Explanation

**Critical — Permanent irrecoverable lock of user funds.**

- For **bridge tokens** (`isBridgeToken[tokenAddress]`): `BridgeToken.burn` is called at line 405; the tokens are destroyed on EVM and can never be minted on NEAR.
- For **native ERC-20 tokens**: `safeTransferFrom` moves them into the bridge contract at line 407–412; they are permanently stranded with no withdrawal path.
- For **native ETH** (`tokenAddress == address(0)`): ETH is forwarded to the contract; no recovery function exists.

This matches the allowed impact: *"Critical. Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

---

### Likelihood Explanation

**Medium.** The entry point is fully public and requires no privilege. Realistic trigger scenarios include:

1. A front-end bug that submits an empty or partially-constructed recipient field.
2. A user manually constructing a transaction with a typo or missing chain prefix (e.g., `"alice.near"` instead of `"near:alice.near"`).
3. A third-party integration that omits the recipient field.

The same gap exists in `initTransfer1155` and in the Solana `init_transfer` (`solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs`, line 72 only checks `amount > fee`; `recipient: String` is unchecked).

---

### Recommendation

Add an explicit non-empty guard on `recipient` (and `initTransfer1155`) before any token movement:

```solidity
require(bytes(recipient).length > 0, "ERR_EMPTY_RECIPIENT");
```

For stronger protection, validate the `chain:address` prefix format on-chain (e.g., require the presence of `:` and a minimum length). Analogously, add a non-empty check in the Solana `InitTransfer::process` before `post_message`.

---

### Proof of Concept

**Attacker-controlled entry path:**

1. Attacker (or user) calls:
   ```solidity
   OmniBridge.initTransfer(
       tokenAddress,   // any registered ERC-20
       1000,           // amount
       0,              // fee
       0,              // nativeFee
       "",             // ← empty recipient — no revert
       ""
   );
   ```
2. `fee >= amount` check: `0 >= 1000` → false → **no revert**.
3. `IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), 1000)` executes — tokens leave the user.
4. `emit InitTransfer(..., "", ...)` — event recorded with empty recipient.
5. Relayer submits proof to NEAR `fin_transfer`.
6. NEAR prover parses the EVM event; `OmniAddress::from_str("")` → `ERR_INVALID_HEX`.
7. `fin_transfer_callback` hits `env::panic_str(BridgeError::InvalidProofMessage)`.
8. Transfer is never marked finalised; every retry panics identically.
9. **1000 tokens are permanently locked in the EVM bridge contract with no recovery path.** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** near/omni-types/src/lib.rs (L389-411)
```rust
impl FromStr for OmniAddress {
    type Err = String;

    fn from_str(input: &str) -> Result<Self, Self::Err> {
        let (chain, recipient) = input.split_once(':').unwrap_or(("eth", input));

        match chain {
            "eth" => Ok(Self::Eth(recipient.parse().map_err(stringify)?)),
            "near" => Ok(Self::Near(recipient.parse().map_err(stringify)?)),
            "sol" => Ok(Self::Sol(recipient.parse().map_err(stringify)?)),
            "arb" => Ok(Self::Arb(recipient.parse().map_err(stringify)?)),
            "base" => Ok(Self::Base(recipient.parse().map_err(stringify)?)),
            "bnb" => Ok(Self::Bnb(recipient.parse().map_err(stringify)?)),
            "pol" => Ok(Self::Pol(recipient.parse().map_err(stringify)?)),
            "hlevm" => Ok(Self::HyperEvm(recipient.parse().map_err(stringify)?)),
            "abs" => Ok(Self::Abs(recipient.parse().map_err(stringify)?)),
            "btc" => Ok(Self::Btc(recipient.to_string())),
            "zcash" => Ok(Self::Zcash(recipient.to_string())),
            "strk" => Ok(Self::Strk(recipient.parse().map_err(stringify)?)),
            "fogo" => Ok(Self::Fogo(recipient.parse().map_err(stringify)?)),
            _ => Err(format!("Chain {chain} is not supported")),
        }
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

**File:** near/omni-types/src/tests/lib_test.rs (L272-282)
```rust
        (
            "invalid_format".to_string(),
            Err("ERR_INVALID_HEX".to_string()),
            "Should fail on missing chain prefix",
        ),
        (
            "unknown:address".to_string(),
            Err("Chain unknown is not supported".to_string()),
            "Should fail on unsupported chain",
        ),
    ];
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs (L71-73)
```rust
impl InitTransfer<'_> {
    pub fn process(&self, payload: &InitTransferPayload) -> Result<()> {
        require!(payload.amount > payload.fee, ErrorCode::InvalidFee);
```
