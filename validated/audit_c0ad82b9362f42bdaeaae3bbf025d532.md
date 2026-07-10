### Title
Missing Validation of Empty `recipient` String in `initTransfer` Causes Permanent Irrecoverable Lock of User Funds — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

The `initTransfer` function in `OmniBridge.sol` (and its analogs in StarkNet and Solana) accepts a `recipient` string parameter with no non-empty validation. If a user passes an empty string `""`, their tokens are irreversibly locked or burned on the source chain while the NEAR bridge is unable to finalize the transfer, resulting in permanent loss of funds with no recovery path.

---

### Finding Description

In `OmniBridge.sol`, `initTransfer` accepts `string calldata recipient` as the destination-chain address. The function performs no check that this string is non-empty before locking or burning the caller's tokens: [1](#0-0) 

The only input guards present are a fee-vs-amount check and a pause check. There is no `require(bytes(recipient).length > 0, ...)` guard.

Depending on `tokenAddress`, the function either:
- Burns bridge tokens via `BridgeToken.burn` (irreversible), or
- Transfers ERC-20 tokens into the contract via `safeTransferFrom` (locked), or
- Accepts native ETH from `msg.value` (locked). [2](#0-1) 

After locking/burning, the Wormhole variant (`OmniBridgeWormhole`) publishes the message including the empty recipient string: [3](#0-2) 

On the NEAR side, `fin_transfer_callback` decodes the prover result into a `ProverResult::InitTransfer`, where `recipient` must deserialize as a valid `OmniAddress`. An empty string cannot be parsed as any `OmniAddress` variant, causing the NEAR callback to panic: [4](#0-3) 

There is no refund or recovery mechanism on the EVM side for a failed NEAR-side finalization. The funds are permanently stranded.

The same root cause exists in the StarkNet bridge: [5](#0-4) 

And in the Solana bridge, where `InitTransferPayload.recipient` is a `String` with no non-empty check in either `InitTransfer::process` or `InitTransferSol::process`: [6](#0-5) [7](#0-6) [8](#0-7) 

---

### Impact Explanation

**Permanent freezing / irrecoverable lock of user funds** (High/Critical).

Once `initTransfer` executes with an empty `recipient`:
- ERC-20 or ETH is locked inside `OmniBridge` (or bridge tokens are burned) — both are irreversible at the EVM layer.
- The NEAR bridge cannot finalize the transfer because the empty string fails `OmniAddress` deserialization.
- No admin escape hatch or refund function exists in `OmniBridge.sol` to recover stuck tokens.

The user's entire `amount` is permanently lost.

---

### Likelihood Explanation

**Low-to-medium.** The function is publicly callable by any token holder. An empty string is a trivially reachable input — a frontend bug, a direct contract call, a scripting error, or a copy-paste mistake can all produce `recipient = ""`. No privileged role is required. The same surface exists on three separate chain deployments (EVM, StarkNet, Solana), multiplying exposure.

---

### Recommendation

Add a non-empty guard on `recipient` in every `initTransfer` entry point before any token movement occurs:

**EVM (`OmniBridge.sol`):**
```solidity
require(bytes(recipient).length > 0, "OmniBridge: empty recipient");
```
Place this immediately after the fee check at line 382.

**StarkNet (`omni_bridge.cairo`):**
```cairo
assert(recipient.len() > 0, 'ERR_EMPTY_RECIPIENT');
```

**Solana (`InitTransfer::process` and `InitTransferSol::process`):**
```rust
require!(!payload.recipient.is_empty(), ErrorCode::InvalidArgs);
```

Optionally, add a lightweight format check (e.g., prefix matching for `"near:"`, `"eth:"`, etc.) to catch obviously malformed addresses before funds are committed.

---

### Proof of Concept

1. Deploy or interact with the live `OmniBridgeWormhole` contract.
2. Approve `amount` of any ERC-20 token to the bridge.
3. Call:
   ```solidity
   omniBridge.initTransfer(
       tokenAddress,
       amount,   // e.g. 1e18
       0,        // fee
       0,        // nativeFee
       "",       // ← empty recipient
       ""
   );
   ```
4. Observe: tokens are transferred from the caller into the bridge (or burned). `currentOriginNonce` increments. A Wormhole message is published.
5. The NEAR relayer picks up the Wormhole VAA and calls `fin_transfer` on the NEAR bridge.
6. `fin_transfer_callback` attempts to decode the recipient as `OmniAddress` — deserialization fails, the callback panics with `BridgeError::InvalidProofMessage`.
7. The EVM tokens remain locked in `OmniBridge` (or are already burned) with no recovery path. The user's funds are permanently lost.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L373-384)
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
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L394-412)
```text
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
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L129-144)
```text
        bytes memory payload = bytes.concat(
            bytes1(uint8(MessageType.InitTransfer)),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(sender),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(tokenAddress),
            Borsh.encodeUint64(originNonce),
            Borsh.encodeUint128(amount),
            Borsh.encodeUint128(fee),
            Borsh.encodeUint128(nativeFee),
            Borsh.encodeString(recipient),
            Borsh.encodeString(message)
        );
        // slither-disable-next-line reentrancy-eth
        _wormhole.publishMessage{value: value}(
            wormholeNonce,
```

**File:** near/omni-bridge/src/lib.rs (L705-712)
```rust
        let Ok(ProverResult::InitTransfer(init_transfer)) = Self::decode_prover_result(0) else {
            env::panic_str(BridgeError::InvalidProofMessage.to_string().as_str())
        };
        require!(
            self.factories
                .get(&init_transfer.emitter_address.get_chain())
                == Some(init_transfer.emitter_address),
            BridgeError::UnknownFactory.as_ref()
```

**File:** starknet/src/omni_bridge.cairo (L281-293)
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
```

**File:** solana/programs/bridge_token_factory/src/state/message/init_transfer.rs (L8-14)
```rust
pub struct InitTransferPayload {
    pub amount: u128,
    pub recipient: String,
    pub fee: u128,
    pub native_fee: u64,
    pub message: String,
}
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs (L72-73)
```rust
    pub fn process(&self, payload: &InitTransferPayload) -> Result<()> {
        require!(payload.amount > payload.fee, ErrorCode::InvalidFee);
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/init_transfer_sol.rs (L35-38)
```rust
    pub fn process(&self, payload: &InitTransferPayload) -> Result<()> {
        require!(payload.fee == 0, ErrorCode::InvalidFee);
        require!(payload.amount > 0, ErrorCode::InvalidArgs);

```
