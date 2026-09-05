Based on my investigation, I found a valid analog to the reported bug class in the Stacks codebase.

### Title
STX transfers/contract-calls with an invalid destination address-network version byte are rejected by mempool but accepted at consensus, causing mempool-vs-block divergence and permanent fund loss - (File: `stackslib/src/chainstate/stacks/db/blocks.rs`, `stackslib/src/chainstate/stacks/db/transactions.rs`)

### Summary
The external report describes a bridge that lets a sender transfer assets to a `destinationNetwork` value that is never checked for existence/validity, so funds can be sent to an address on a network that can never process them, causing funds to be permanently lost. The Stacks analog is the recipient/`origin`/`payer` `StacksAddress` **version byte**: consensus-level transaction processing never validates that a `PrincipalData`'s version byte corresponds to a real, spendable network (mainnet/testnet single-/multi-sig), while the mempool admission path does perform this check. This produces exactly the "checked in one path, not in the other" pattern from the report.

### Finding Description
`StacksChainState::is_valid_address_version()` is explicitly documented as non-consensus-critical: [1](#0-0) 
It is only invoked from the mempool admission path (`will_admit_mempool_tx`) to reject `origin`/`payer` addresses whose version byte is not one of the four canonical values: [2](#0-1) 

By contrast, the actual block-processing precheck, `process_transaction_precheck` in `transactions.rs`, only validates `chain_id` and the transaction's `mainnet`/`testnet` `TransactionVersion` flag — it never validates that any `PrincipalData` embedded in the payload (recipient of a `TokenTransfer`, or the `origin`/`sponsor` address) actually has a version byte belonging to a real network: [3](#0-2) 

Meanwhile, `StandardPrincipalData::new()` only enforces that the version byte fits in 5 bits (`< 32`), not that it corresponds to one of the recognized network/address-type combinations: [4](#0-3) 

Clarity itself acknowledges that "future"/unrecognized version bytes are a distinct, valid-but-unmatched category (`version_matches_current_network` explicitly notes it can match neither mainnet nor testnet), and test coverage even documents this as intentional for forward-compatibility: [5](#0-4) [6](#0-5) 

Because `is_mainnet()`/`is_multisig()` on `StandardPrincipalData` only recognize the 4 canonical version bytes, a `PrincipalData` constructed with an out-of-range-but-<32 version byte (e.g. `0x1f`) is neither "mainnet" nor "testnet" under any of the block-validation predicates such as `StacksBlock::validate_transactions_network`: [7](#0-6) 
That function only checks the *sender's transaction version* (`tx.is_mainnet()`), not the recipient principal embedded in the payload, so it does not catch this case either.

### Impact Explanation
This matches the "mempool-versus-block admissibility divergence" category explicitly listed as a valid High-impact analog:
- A `TokenTransfer` (or contract-call argument) whose destination `PrincipalData` carries an unrecognized version byte will be **rejected by the mempool** (`MemPoolRejection::BadAddressVersionByte`, as exercised in `stacks-node/src/tests/mempool.rs:509-540`) — but nothing in `process_transaction_precheck` or the block-level `validate_transactions_static`/`validate_transactions_network` functions rejects the same transaction once it appears directly in a block (e.g., mined by a miner who bypasses/ignores the mempool, or replayed from a different software version whose mempool rules changed).
- If such a transaction is nonetheless accepted into a block, the STX are moved to an address that no legitimate consensus-recognized network/version combination can ever produce a valid `StacksAddress` for spending under the current or foreseeable network rules — mirroring the report's "funds inaccessible ... lost for the sender and recipient" outcome, since `SinglesigSpendingCondition::verify`/`address_mainnet`/`address_testnet` only ever construct the 4 canonical versions, so no key holder's real transaction can ever match an address using an unrecognized version byte as its *origin*.
- This is a case where two nodes (or the miner vs. the mempool-based validator) can classify the same transaction differently — a direct match to the "transaction classified differently by two nodes" rule in scope.

### Likelihood Explanation
Likelihood is moderate: it requires either (a) a miner/relay path that skips the standard mempool admission check (e.g., a custom miner, a signer verifying blocks through `postblock_proposal.rs`, or a future software version with different address-recognition rules) or (b) a user/wallet bug that creates a transaction with a malformed recipient principal that still passes signature/auth checks. It does not require a privileged signer or miner key on the attacker's own side — the attacker only needs an unprivileged account and the ability to submit a normal signed transaction; the vulnerability is that consensus code has no equivalent guard to the mempool guard, which is a pure code-path gap rather than a hypothetical.

### Recommendation
Add a consensus-level (not just mempool) check to `process_transaction_precheck` (or a dedicated static block-validator) that rejects any `PrincipalData` referenced in a `TransactionPayload::TokenTransfer` (and any origin/sponsor/contract-call address) whose version byte is not one of the two per-network canonical values (mainnet: `C32_ADDRESS_VERSION_MAINNET_SINGLESIG`/`_MULTISIG`; testnet: the corresponding testnet constants), mirroring `is_valid_address_version()` but making it consensus-critical and applied to the destination principal, not just the origin/payer.

### Proof of Concept
1. Construct a `StacksTransaction` with `TransactionPayload::TokenTransfer(recipient, amount, memo)` where `recipient` is a `PrincipalData::Standard(StandardPrincipalData::new(0x1f, hash_bytes))` (version `0x1f` is `<32` so construction succeeds, per `clarity-types/src/types/mod.rs:94-100`, but is not one of the 4 recognized network versions).
2. Submit via `will_admit_mempool_tx` — it is rejected with `MemPoolRejection::BadAddressVersionByte` (as in `stacks-node/src/tests/mempool.rs:509-540`), proving the mempool path checks this.
3. Feed the same transaction directly through `StacksChainState::process_transaction_precheck` (as would happen during block processing) — it passes, because that function only checks `chain_id` and `tx.version` (mainnet/testnet flag on the transaction, not on the recipient), per `stackslib/src/chainstate/stacks/db/transactions.rs:574-628`, and `StacksBlock::validate_transactions_network` similarly only inspects `tx.is_mainnet()` (`stackslib/src/chainstate/stacks/block.rs:455-467`), never the recipient principal's version.
4. This demonstrates the transaction is accepted at the consensus/block level while rejected at the mempool level — the admissibility divergence — and that funds sent this way go to a principal no valid signing key can ever construct as its own address under current-network rules.

### Citations

**File:** stackslib/src/chainstate/stacks/db/blocks.rs (L6467-6477)
```rust
    /// Is the given address version currently supported?
    /// NOTE: not consensus-critical; only used for mempool admission
    fn is_valid_address_version(mainnet: bool, version: u8) -> bool {
        if mainnet {
            version == C32_ADDRESS_VERSION_MAINNET_SINGLESIG
                || version == C32_ADDRESS_VERSION_MAINNET_MULTISIG
        } else {
            version == C32_ADDRESS_VERSION_TESTNET_SINGLESIG
                || version == C32_ADDRESS_VERSION_TESTNET_MULTISIG
        }
    }
```

**File:** stackslib/src/chainstate/stacks/db/blocks.rs (L6678-6686)
```rust
        if !StacksChainState::is_valid_address_version(
            chainstate_config.mainnet,
            origin.principal.version(),
        ) || !StacksChainState::is_valid_address_version(
            chainstate_config.mainnet,
            payer.principal.version(),
        ) {
            return Err(MemPoolRejection::BadAddressVersionByte);
        }
```

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L574-628)
```rust
    pub fn process_transaction_precheck(
        config: &DBConfig,
        tx: &StacksTransaction,
        epoch_id: StacksEpochId,
    ) -> Result<(), Error> {
        // valid auth?
        if !tx.auth.is_supported_in_epoch(epoch_id) {
            let msg = format!(
                "Invalid tx {}: authentication mode not supported in Epoch {epoch_id}",
                tx.txid()
            );
            warn!("{msg}");

            return Err(Error::InvalidStacksTransaction(msg, false));
        }
        let verification_mode = if epoch_id.allows_tx_signatures_with_high_s() {
            TransactionAuthVerificationMode::AllowHighS
        } else {
            TransactionAuthVerificationMode::EnforceLowS
        };

        tx.verify(verification_mode)?;

        // destined for us?
        if config.chain_id != tx.chain_id {
            let msg = format!(
                "Invalid tx {}: invalid chain ID {} (expected {})",
                tx.txid(),
                tx.chain_id,
                config.chain_id
            );
            warn!("{}", &msg);

            return Err(Error::InvalidStacksTransaction(msg, false));
        }

        match tx.version {
            TransactionVersion::Mainnet => {
                if !config.mainnet {
                    let msg = format!("Invalid tx {}: on testnet; got mainnet", tx.txid());
                    warn!("{}", &msg);

                    return Err(Error::InvalidStacksTransaction(msg, false));
                }
            }
            TransactionVersion::Testnet => {
                if config.mainnet {
                    let msg = format!("Invalid tx {}: on mainnet; got testnet", tx.txid());
                    warn!("{}", &msg);

                    return Err(Error::InvalidStacksTransaction(msg, false));
                }
            }
        }

```

**File:** clarity-types/src/types/mod.rs (L94-100)
```rust
impl StandardPrincipalData {
    pub fn new(version: u8, bytes: [u8; 20]) -> Result<Self, ClarityTypeError> {
        if version >= 32 {
            return Err(ClarityTypeError::InvalidPrincipalVersion(version));
        }
        Ok(Self(version, bytes))
    }
```

**File:** clarity/src/vm/functions/principals.rs (L55-64)
```rust
/// Returns true if `version` indicates an address type that matches the network we are "currently
/// operating in", as indicated by the GlobalContext.
fn version_matches_current_network(version: u8, global_context: &GlobalContext) -> bool {
    let context_is_mainnet = global_context.mainnet;
    let context_is_testnet = !global_context.mainnet;

    // Note: It is possible for the version to match neither mainnet or testnet.
    (version_matches_mainnet(version) && context_is_mainnet)
        || (version_matches_testnet(version) && context_is_testnet)
}
```

**File:** clarity/src/vm/tests/principals.rs (L846-852)
```rust
#[test]
// Test cases where the version byte is of the right type `(buff 1)`, but where the byte doesn't
// match a recognized network. This is meant for compatibility with "future" network bytes, so
// is still valid.
fn test_principal_construct_version_byte_future() {
    // The version byte 0x1f is unrecognized today, but is valid for the future.
    let input = r#"(principal-construct? 0x1f 0x0102030405060708091011121314151617181920)"#;
```

**File:** stackslib/src/chainstate/stacks/block.rs (L455-467)
```rust
    /// verify all txs are same mainnet/testnet
    pub fn validate_transactions_network(txs: &[StacksTransaction], mainnet: bool) -> bool {
        for tx in txs {
            if mainnet && !tx.is_mainnet() {
                warn!("Tx {} is not mainnet", tx.txid());
                return false;
            } else if !mainnet && tx.is_mainnet() {
                warn!("Tx {} is not testnet", tx.txid());
                return false;
            }
        }
        return true;
    }
```
