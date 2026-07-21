### Title
Silent DA Mode Downgrade in FGW Sync Corrupts V3 Transaction Hash Binding - (File: crates/apollo_starknet_client/src/reader/objects/transaction.rs)

### Summary
The `From<ReservedDataAvailabilityMode>` implementation unconditionally returns `DataAvailabilityMode::L1` regardless of the actual input value. When a V3 transaction carrying `nonce_data_availability_mode = 1` (L2) or `fee_data_availability_mode = 1` (L2) is ingested from the Feeder Gateway during state synchronisation, both DA-mode fields are silently downgraded to `L1`. Because these fields are direct inputs to the Poseidon transaction-hash function, the hash the sequencer computes and stores diverges from the canonical hash that was committed on-chain, binding the wrong hash to the transaction payload.

### Finding Description
In `crates/apollo_starknet_client/src/reader/objects/transaction.rs` the conversion from the wire-level enum to the internal type ignores its argument entirely:

```rust
impl From<ReservedDataAvailabilityMode>
    for starknet_api::data_availability::DataAvailabilityMode
{
    // TODO(Arni): Fix this. Support L2 data availability mode.
    fn from(_: ReservedDataAvailabilityMode) -> Self {
        starknet_api::data_availability::DataAvailabilityMode::L1   // always L1
    }
}
``` [1](#0-0) 

The enum itself carries both variants, so the deserialiser can produce `L2`:

```rust
pub enum ReservedDataAvailabilityMode {
    Reserved = 0,
    L2 = 1,
}
``` [2](#0-1) 

This `.into()` call is invoked for every V3 transaction type during sync:

- `IntermediateDeclareTransaction → DeclareTransactionV3` (lines 321–334)
- `IntermediateDeployAccountTransaction → DeployAccountTransactionV3` (lines 472–487)
- `IntermediateInvokeTransaction → InvokeTransactionV3` (lines 623–636) [3](#0-2) [4](#0-3) 

The corrupted `nonce_data_availability_mode` / `fee_data_availability_mode` values are then passed directly into the Poseidon hash chain via `concat_data_availability_mode`:

```rust
let data_availability_mode = concat_data_availability_mode(
    transaction.nonce_data_availability_mode(),
    transaction.fee_data_availability_mode(),
);
// … chained into the Poseidon hash
.chain(&data_availability_mode)
``` [5](#0-4) 

`DataAvailabilityMode::L1 = 0` and `DataAvailabilityMode::L2 = 1` produce different felt values, so replacing `L2` with `L1` changes the Poseidon digest and yields a transaction hash that does not match the hash committed in the block. [6](#0-5) 

### Impact Explanation
Any V3 transaction synced from the FGW with `nonce_data_availability_mode = 1` or `fee_data_availability_mode = 1` will be stored under a wrong transaction hash. Every downstream consumer — `starknet_getTransactionByHash`, `starknet_getTransactionReceipt`, fee estimation, tracing, and simulation — will either fail to locate the transaction or return results keyed to the wrong hash. This matches **High: Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload** and **High: RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value**.

### Likelihood Explanation
The gateway stateless validator currently rejects any incoming transaction whose DA mode is not `L1`: [7](#0-6) 

However, this guard applies only to the **gateway admission path**. The **sync path** (FGW reader) has no equivalent check; it relies solely on the broken `From` impl. The `ReservedDataAvailabilityMode::L2 = 1` variant is already present in the enum and will be exercised the moment the Starknet protocol activates L2 DA mode and the FGW begins emitting `1` for these fields. The TODO comment confirms this is a planned activation, not a hypothetical one. [8](#0-7) 

### Recommendation
Replace the wildcard `_` pattern with a proper match that preserves the actual variant:

```diff
 impl From<ReservedDataAvailabilityMode>
     for starknet_api::data_availability::DataAvailabilityMode
 {
-    // TODO(Arni): Fix this. Support L2 data availability mode.
-    fn from(_: ReservedDataAvailabilityMode) -> Self {
-        starknet_api::data_availability::DataAvailabilityMode::L1
+    fn from(mode: ReservedDataAvailabilityMode) -> Self {
+        match mode {
+            ReservedDataAvailabilityMode::Reserved => DataAvailabilityMode::L1,
+            ReservedDataAvailabilityMode::L2      => DataAvailabilityMode::L2,
+        }
     }
 }
```

Once the fix is in place, rename `Reserved` to `L1` and the enum to `DataAvailabilityMode` as the existing TODO suggests, then remove the wrapper entirely.

### Proof of Concept
1. Construct a FGW JSON block response containing a V3 invoke transaction with `"nonce_data_availability_mode": 1`.
2. Feed it through `IntermediateInvokeTransaction` deserialisation followed by `TryFrom<IntermediateInvokeTransaction> for InvokeTransactionV3`.
3. Observe that the resulting `InvokeTransactionV3.nonce_data_availability_mode` is `L1` (0) instead of `L2` (1).
4. Call `get_invoke_transaction_v3_hash` on the converted struct and compare the output with the hash computed using the original `L2` value — the two hashes differ, confirming the wrong hash is bound to the transaction.

### Citations

**File:** crates/apollo_starknet_client/src/reader/objects/transaction.rs (L169-185)
```rust
// TODO(Arni): Replace this enum with `starknet_api::data_availability::DataAvailabilityMode`
// This enum is required since the FGW sends this field with value 0 as a reserved value. Once the
// feature will be activated this enum should be removed from here and taken from starknet-api.
#[derive(Debug, Deserialize_repr, Serialize_repr, Clone, Eq, PartialEq)]
#[repr(u8)]
pub enum ReservedDataAvailabilityMode {
    // TODO(Arni): Change `Reserved` to `L1`, an the name of the enum to `DataAvailabilityMode`.
    Reserved = 0,
    L2 = 1,
}

impl From<ReservedDataAvailabilityMode> for starknet_api::data_availability::DataAvailabilityMode {
    // TODO(Arni): Fix this. Support L2 data availability mode.
    fn from(_: ReservedDataAvailabilityMode) -> Self {
        starknet_api::data_availability::DataAvailabilityMode::L1
    }
}
```

**File:** crates/apollo_starknet_client/src/reader/objects/transaction.rs (L321-334)
```rust
            nonce_data_availability_mode: declare_tx
                .nonce_data_availability_mode
                .ok_or(ReaderClientError::BadTransaction {
                    tx_hash: declare_tx.transaction_hash,
                    msg: "Declare V3 must contain nonce_data_availability_mode field.".to_string(),
                })?
                .into(),
            fee_data_availability_mode: declare_tx
                .fee_data_availability_mode
                .ok_or(ReaderClientError::BadTransaction {
                    tx_hash: declare_tx.transaction_hash,
                    msg: "Declare V3 must contain fee_data_availability_mode field.".to_string(),
                })?
                .into(),
```

**File:** crates/apollo_starknet_client/src/reader/objects/transaction.rs (L623-636)
```rust
            nonce_data_availability_mode: invoke_tx
                .nonce_data_availability_mode
                .ok_or(ReaderClientError::BadTransaction {
                    tx_hash: invoke_tx.transaction_hash,
                    msg: "Invoke V3 must contain nonce_data_availability_mode field.".to_string(),
                })?
                .into(),
            fee_data_availability_mode: invoke_tx
                .fee_data_availability_mode
                .ok_or(ReaderClientError::BadTransaction {
                    tx_hash: invoke_tx.transaction_hash,
                    msg: "Invoke V3 must contain fee_data_availability_mode field.".to_string(),
                })?
                .into(),
```

**File:** crates/starknet_api/src/transaction_hash.rs (L379-396)
```rust
    let data_availability_mode = concat_data_availability_mode(
        transaction.nonce_data_availability_mode(),
        transaction.fee_data_availability_mode(),
    );
    let account_deployment_data_hash = HashChain::new()
        .chain_iter(transaction.account_deployment_data().0.iter())
        .get_poseidon_hash();
    let calldata_hash =
        HashChain::new().chain_iter(transaction.calldata().0.iter()).get_poseidon_hash();
    let mut hash_chain = HashChain::new()
        .chain(&INVOKE)
        .chain(&transaction_version.0)
        .chain(transaction.sender_address().0.key())
        .chain(&tip_resource_bounds_hash)
        .chain(&paymaster_data_hash)
        .chain(&Felt::try_from(chain_id)?)
        .chain(&transaction.nonce().0)
        .chain(&data_availability_mode)
```

**File:** crates/starknet_api/src/data_availability.rs (L63-69)
```rust
impl From<DataAvailabilityMode> for Felt {
    fn from(data_availability_mode: DataAvailabilityMode) -> Felt {
        match data_availability_mode {
            DataAvailabilityMode::L1 => Felt::ZERO,
            DataAvailabilityMode::L2 => Felt::ONE,
        }
    }
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L197-212)
```rust
    /// The Starknet OS enforces that the nonce data availability mode is L1. We add this validation
    /// here in the gateway to prevent transactions from failing the OS.
    fn validate_nonce_data_availability_mode(
        &self,
        tx: &RpcTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        let expected_da_mode = DataAvailabilityMode::L1;
        let da_mode = *tx.nonce_data_availability_mode();
        if da_mode != expected_da_mode {
            return Err(StatelessTransactionValidatorError::InvalidDataAvailabilityMode {
                field_name: "nonce".to_string(),
            });
        };

        Ok(())
    }
```
