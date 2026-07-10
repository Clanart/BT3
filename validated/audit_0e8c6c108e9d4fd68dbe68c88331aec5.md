The critical piece is in `near/omni-types/src/evm/events.rs` line 127, where the EVM prover parses the recipient string. Let me verify the full chain.

The full attack chain is traceable through production code. Here is the complete analysis:

---

### Title
Malformed `recipient` in `ACTION_INIT_TRANSFER` Burns EVM Tokens With No Recoverable NEAR Settlement — (`evm/src/omni-bridge/contracts/HlBridgeToken.sol` / `OmniBridge.sol`)

### Summary

A HyperCore user who supplies a syntactically valid but `OmniAddress`-unroutable recipient string (empty string, unsupported chain prefix, invalid NEAR account ID) through `coreReceiveWithData` ACTION_INIT_TRANSFER will cause EVM-side tokens to be permanently burned while the NEAR prover permanently rejects every subsequent `fin_transfer` attempt for that nonce, violating the bridge invariant that every EVM burn must be matched by a NEAR settlement.

### Finding Description

**Step 1 — EVM: no recipient validation before burn**

In `coreReceiveWithData`, the `recipient` string is ABI-decoded from user-controlled `data` with zero format validation: [1](#0-0) 

The decoded `recipient` is forwarded directly to `OmniBridge.initTransfer`. Inside `initTransfer`, there is also no validation of the recipient string: [2](#0-1) 

The token burn at line 405 and the `InitTransfer` event emission at line 427 both execute unconditionally regardless of whether `recipient` is parseable as an `OmniAddress`.

**Step 2 — EVM Prover: hard parse failure on malformed recipient**

When the NEAR relayer submits `fin_transfer` with a Merkle proof of the `InitTransfer` event, the EVM prover's `verify_proof_callback` calls `parse_evm_proof`, which calls `TryFromLog` for `InitTransferMessage`. At line 127: [3](#0-2) 

This calls `OmniAddress::from_str` on the raw recipient string. The parser: [4](#0-3) 

- Empty string `""` → defaults to `"eth"` chain, `"".parse::<H160>()` → `Err("ERR_INVALID_HEX")`
- `"unsupported:foo"` → `Err("Chain unsupported is not supported")`
- `"near:invalid account id"` → `Err(...)` from NEAR `AccountId` parser

The `?` propagates the error; `verify_proof_callback` returns `Err`, which is the promise result seen by `fin_transfer_callback`.

**Step 3 — NEAR bridge: permanent panic, no state stored**

`fin_transfer_callback` pattern-matches the prover result: [5](#0-4) 

When the prover returned `Err`, `decode_prover_result(0)` is not `Ok(ProverResult::InitTransfer(...))`, so the `else` branch fires `env::panic_str(BridgeError::InvalidProofMessage)`. The callback panics before any state change. The transfer is never inserted into `pending_transfers`. The origin nonce is never marked finalised.

Because the `InitTransfer` event log is immutable on-chain, every future `fin_transfer` attempt for this nonce will produce the same prover error. There is no admin rescue path, no refund mechanism, and no way to re-route the burned tokens.

### Impact Explanation

Every token burned by `BridgeToken.burn` in `initTransfer` is irrecoverably lost. The bridge collateral invariant — that every EVM-side burn is matched by a NEAR-side mint or unlock — is permanently violated for any transfer whose `recipient` string is not a valid `OmniAddress`. The affected user loses 100% of the bridged amount with no recourse.

### Likelihood Explanation

The `coreReceiveWithData` path is the only EVM bridge entry point where the caller (a HyperCore user via `sendToEvmWithData`) supplies the recipient as a free-form string rather than a typed `OmniAddress`. A user who mistypes the chain prefix (e.g., `"ethereum:0x..."` instead of `"eth:0x..."`), omits the prefix entirely for a non-EVM chain, or provides an empty string will trigger this path. The HyperCore UI/SDK has no on-chain guard to prevent it.

### Recommendation

Validate the `recipient` string as a parseable `OmniAddress` **before** executing `_update` and calling `initTransfer`. The simplest fix is to add a Solidity helper that attempts to parse the recipient string off-chain (or via a precompile) and revert if it is not a known `chain:address` format. Alternatively, require the recipient to be passed as a structured type (chain ID + raw bytes) rather than a free-form string, mirroring how the NEAR-side `InitTransferMsg` uses a typed `OmniAddress` field.

### Proof of Concept

```
// In HlBridgeToken.ts (or equivalent Hardhat test):
const malformedRecipient = ""          // or "unsupported:foo"
const data = ethers.concat([
  "0x01",
  ethers.AbiCoder.defaultAbiCoder().encode(
    ["uint128", "string", "string"],
    [0n, malformedRecipient, ""]
  ),
])

// 1. systemSigner calls coreReceiveWithData — succeeds, tokens burned
await token.connect(systemSigner).coreReceiveWithData(
  user.address, ethers.ZeroHash, 0, AMOUNT, 0, data
)
// Assert: totalSupply == 0, InitTransfer event emitted with malformed recipient

// 2. On NEAR: construct fin_transfer proof for this event
// Assert: parse_evm_proof returns Err("Chain unsupported is not supported")
//         fin_transfer_callback panics with InvalidProofMessage
//         pending_transfers unchanged, tokens permanently lost
``` [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** evm/src/omni-bridge/contracts/HlBridgeToken.sol (L106-141)
```text
    function coreReceiveWithData(
        address from,
        bytes32 /*destinationRecipient*/,
        uint32 /*destinationChainId*/,
        uint256 amount,
        uint64 /*coreNonce*/,
        bytes calldata data
    ) external override {
        if (msg.sender != _systemAddress) revert NotSystemAddress();
        if (data.length == 0) revert EmptyActionData();

        uint8 action = uint8(data[0]);
        bytes calldata tail = data[1:];

        if (action == ACTION_TRANSFER) {
            address recipient = abi.decode(tail, (address));
            _update(_systemAddress, recipient, amount);
        } else if (action == ACTION_INIT_TRANSFER) {
            (uint128 fee, string memory recipient, string memory message) = abi
                .decode(tail, (uint128, string, string));
            uint128 amount128 = amount.toUint128();
            _update(_systemAddress, address(this), amount);
            IOmniBridgeInitTransfer(owner()).initTransfer(
                address(this),
                amount128,
                fee,
                0,
                recipient,
                message
            );
        } else {
            revert UnknownAction(action);
        }

        emit CoreReceived(from, action, amount, data);
    }
```

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

**File:** near/omni-types/src/evm/events.rs (L115-135)
```rust
impl TryFromLog<Log<InitTransfer>> for InitTransferMessage {
    type Error = String;

    fn try_from_log(chain_kind: ChainKind, event: Log<InitTransfer>) -> Result<Self, Self::Error> {
        Ok(Self {
            emitter_address: OmniAddress::new_from_evm_address(
                chain_kind,
                H160(event.address.into()),
            )?,
            origin_nonce: event.data.originNonce,
            token: OmniAddress::new_from_evm_address(chain_kind, H160(event.tokenAddress.into()))?,
            amount: near_sdk::json_types::U128(event.data.amount),
            recipient: event.data.recipient.parse().map_err(stringify)?,
            fee: Fee {
                fee: near_sdk::json_types::U128(event.data.fee),
                native_fee: near_sdk::json_types::U128(event.data.nativeTokenFee),
            },
            sender: OmniAddress::new_from_evm_address(chain_kind, H160(event.data.sender.into()))?,
            msg: event.data.message,
        })
    }
```

**File:** near/omni-types/src/lib.rs (L392-411)
```rust
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

**File:** near/omni-bridge/src/lib.rs (L700-713)
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
```
