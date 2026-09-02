## Analog Vulnerability Found

### Title
Duplicate public keys in `SecurityCouncil` configuration let fewer than the intended number of distinct signers satisfy the M‑of‑N multisig threshold - (File: `core/src/builder/script.rs`, `core/src/deposit.rs`)

### Summary
The `SecurityCouncil` configuration structure and the `Multisig` script it builds do not check that the list of security‑council public keys is free of duplicates. Because the on‑chain script sums independent `OP_CHECKSIG`/`OP_CHECKSIGADD` results against a `threshold`, a duplicated public key lets the *same* signature be reused to satisfy two "slots" in the threshold count, effectively lowering the number of distinct co-signers required below the configured `threshold`. This mirrors the reported bug class in `RevenueManagement.setOutputTokensConfig`, where repeated array elements let a single configuration entry be double‑counted against a fixed limit (there, the 100% ratio cap; here, the M‑of‑N signer cap).

### Finding Description
`SecurityCouncil` is parsed from a colon/comma separated string (`threshold:pk1,pk2,...`) in `core/src/deposit.rs`: [1](#0-0) 

The parser only validates that `pks` is non-empty and that `threshold <= pks.len()`; it never checks that all `pks` entries are unique.

This `SecurityCouncil` is turned directly into a `Multisig` script via `Multisig::from_security_council`: [2](#0-1) 

And the script itself is built as a simple `CHECKSIG` + repeated `CHECKSIGADD` chain compared against `threshold` with `OP_NUMEQUAL`: [3](#0-2) 

Because Schnorr `OP_CHECKSIG`/`OP_CHECKSIGADD` verify a signature against the specific pubkey pushed at that script position independently of other positions, if the same `XOnlyPublicKey` appears at two positions in `pubkeys`, one holder of that single private key can produce one valid Schnorr signature and place it in the witness slots corresponding to *both* occurrences of the duplicated key (a single signature over the same sighash is valid for every position where that same pubkey occurs, since the `SIGHASH` message doesn't encode the pubkey/script position). This adds 2 to the `OP_NUMEQUAL` counter for the effort of one distinct signer, satisfying a `threshold` that was meant to represent that many *distinct* security-council members.

This `Multisig`/`SecurityCouncil` construct directly gates value custody:
- It is the script used in `ReplacementDepositScript`/replacement deposits (`core/src/deposit.rs` `get_deposit_scripts`, `core/src/builder/address.rs generate_replacement_deposit_address`) — the mechanism that lets a security council re-point a stuck/buggy deposit's vault funds. [4](#0-3) 
- It is the sole output script of `create_emergency_stop_txhandler`, which moves the entire bridge-amount UTXO out of the move‑to‑vault output under only the security council's authorization: [5](#0-4) 

None of the deposit-validation code that checks for duplicate elements elsewhere in the protocol (verifiers, watchtowers, operators) covers the `SecurityCouncil.pks` list: [6](#0-5) [7](#0-6) 

`is_deposit_valid` in the verifier only compares `deposit_data.security_council != self.config.security_council` for equality with the verifier's own configured value, and never asserts uniqueness within the `pks` vector: [8](#0-7) 

### Impact Explanation
The security council is the last-resort authority that can move a locked BTC vault UTXO (via `EmergencyStop`) or redirect a deposit (`ReplacementDeposit`) outside of the normal N-of-N/BitVM flow. If its threshold configuration contains a duplicated key (whether by operator misconfiguration or malicious council setup), the *effective* number of independent human/key custodians required to authorize movement of funds is silently reduced — potentially to as few as `threshold - 1` distinct signers (or even 1, if enough duplication is present). This breaks the intended binding: "N distinct authorized custodians consented" vs. "fewer distinct custodians actually consented," directly enabling BTC to leave a vault UTXO (via emergency-stop) with less than the intended authorization, which maps to the Critical impact bucket: "BTC leaving a move-to-vault UTXO without a matching fronted withdrawal" / unauthorized signing over custody funds.

### Likelihood Explanation
This does not require any privileged role compromise or majority-hashrate assumption — it only requires that the `SecurityCouncil` string is configured (or accepted from a deposit request) with a duplicate key, which the code does nothing to reject. The `SecurityCouncil` is parsed straight from a config string (`core/src/config/env.rs`) and also travels as part of `DepositData` sent by users/aggregator to verifiers, so any path that constructs a `DepositData`/`SecurityCouncil` without server-side uniqueness enforcement is exposed. Because `is_deposit_valid` only checks council equality against the node's local config (not internal uniqueness of that config), if the locally configured `security_council` itself has a duplicate, every verifier will accept it as "matching," so this is a genuine configuration-time gap, not merely "deployment ignoring documented configuration" — the code has no check available to catch it even if desired.

### Recommendation
Add an explicit uniqueness check for `SecurityCouncil.pks` (analogous to `are_all_verifiers_unique` / `are_all_watchtowers_unique` / `are_all_operators_unique`), enforced both:
1. In `SecurityCouncil::from_str` (`core/src/deposit.rs`) so no code path can construct a `SecurityCouncil` with duplicate keys, and
2. In `is_deposit_valid` (`core/src/verifier.rs`) as a defense-in-depth check on `deposit_data.security_council.pks`, rejecting deposits whose security council contains repeated public keys.

### Proof of Concept
1. Configure (or submit as part of `DepositData`) a `SecurityCouncil` string such as `2:pkA,pkA,pkB` (threshold 2, with `pkA` duplicated).
2. `Multisig::from_security_council` builds the script `<pkA> CHECKSIG <pkA> CHECKSIGADD <pkB> CHECKSIGADD 2 NUMEQUAL`.
3. Holder of `pkA`'s private key alone signs the `EmergencyStop`/`ReplacementDeposit` sighash once, and supplies that single signature in both witness slots corresponding to the two `pkA` occurrences (leaving `pkB`'s slot empty).
4. The script's running `OP_CHECKSIGADD` counter reaches `2`, satisfying `threshold=2`, even though only one distinct security council member actually signed — moving the vault's BTC via `EmergencyStop`/replacement without the intended second independent custodian's consent.

*(Note: this scenario was derived from static code reading of the `Multisig` script builder and `SecurityCouncil` parsing/validation logic; I was not able to run the Bitcoin Script interpreter to empirically confirm signature reuse across duplicate `OP_CHECKSIG` pubkey pushes for the exact same sighash message, though this follows directly from standard Tapscript `OP_CHECKSIG`/`OP_CHECKSIGADD` semantics, which validate a signature against a specific pubkey and message independent of script position.)*

### Citations

**File:** core/src/deposit.rs (L206-217)
```rust
            DepositType::ReplacementDeposit(replacement_deposit_data) => {
                let deposit_script: Arc<dyn SpendableScript> =
                    Arc::new(ReplacementDepositScript::new(
                        nofn_xonly_pk,
                        replacement_deposit_data.old_move_txid,
                    ));
                let security_council_script: Arc<dyn SpendableScript> = Arc::new(
                    Multisig::from_security_council(self.security_council.clone()),
                );

                Ok(vec![deposit_script, security_council_script])
            }
```

**File:** core/src/deposit.rs (L221-237)
```rust
    /// Checks if all verifiers are unique.
    pub fn are_all_verifiers_unique(&self) -> bool {
        let set: HashSet<_> = self.actors.verifiers.iter().collect();
        set.len() == self.actors.verifiers.len()
    }

    /// Checks if all watchtowers are unique.
    pub fn are_all_watchtowers_unique(&self) -> bool {
        let set: HashSet<_> = self.get_watchtowers().into_iter().collect();
        set.len() == self.get_num_watchtowers()
    }

    /// Checks if all operators are unique.
    pub fn are_all_operators_unique(&self) -> bool {
        let set: HashSet<_> = self.actors.operators.iter().collect();
        set.len() == self.actors.operators.len()
    }
```

**File:** core/src/deposit.rs (L259-303)
```rust
impl std::str::FromStr for SecurityCouncil {
    type Err = eyre::Report;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        let mut parts = s.split(':');
        let threshold_str = parts
            .next()
            .ok_or_else(|| eyre::eyre!("Missing threshold"))?;
        let pks_str = parts
            .next()
            .ok_or_else(|| eyre::eyre!("Missing public keys"))?;

        if parts.next().is_some() {
            return Err(eyre::eyre!("Too many parts in security council string"));
        }

        let threshold = threshold_str
            .parse::<u32>()
            .map_err(|e| eyre::eyre!("Invalid threshold: {}", e))?;

        let pks: Result<Vec<XOnlyPublicKey>, _> = pks_str
            .split(',')
            .map(|pk_str| {
                let bytes = hex::decode(pk_str)
                    .map_err(|e| eyre::eyre!("Invalid hex in public key: {}", e))?;
                XOnlyPublicKey::from_slice(&bytes)
                    .map_err(|e| eyre::eyre!("Invalid public key: {}", e))
            })
            .collect();

        let pks = pks?;

        if pks.is_empty() {
            return Err(eyre::eyre!("No public keys provided"));
        }

        if threshold > pks.len() as u32 {
            return Err(eyre::eyre!(
                "Threshold cannot be greater than number of public keys"
            ));
        }

        Ok(SecurityCouncil { pks, threshold })
    }
}
```

**File:** core/src/builder/script.rs (L247-258)
```rust
    fn to_script_buf(&self) -> ScriptBuf {
        let mut script_builder = Builder::new()
            .push_x_only_key(&self.pubkeys[0])
            .push_opcode(OP_CHECKSIG);
        for pubkey in self.pubkeys.iter().skip(1) {
            script_builder = script_builder.push_x_only_key(pubkey);
            script_builder = script_builder.push_opcode(OP_CHECKSIGADD);
        }
        script_builder = script_builder.push_int(self.threshold as i64);
        script_builder = script_builder.push_opcode(OP_NUMEQUAL);
        script_builder.into_script()
    }
```

**File:** core/src/builder/script.rs (L261-271)
```rust
impl Multisig {
    pub fn new(pubkeys: Vec<XOnlyPublicKey>, threshold: u32) -> Self {
        Self { pubkeys, threshold }
    }

    pub fn from_security_council(security_council: SecurityCouncil) -> Self {
        Self {
            pubkeys: security_council.pks,
            threshold: security_council.threshold,
        }
    }
```

**File:** core/src/builder/transaction/mod.rs (L360-384)
```rust
pub fn create_emergency_stop_txhandler(
    deposit_data: &mut DepositData,
    move_to_vault_txhandler: &TxHandler,
    paramset: &'static ProtocolParamset,
) -> Result<TxHandler<Unsigned>, BridgeError> {
    // Hand calculated, total tx size is 11 + 126 * NUM_EMERGENCY_STOPS
    const EACH_EMERGENCY_STOP_VBYTES: Amount = Amount::from_sat(126);
    let security_council = deposit_data.security_council.clone();

    let builder = TxHandlerBuilder::new(TransactionType::EmergencyStop)
        .add_input(
            NormalSignatureKind::NotStored,
            move_to_vault_txhandler.get_spendable_output(UtxoVout::DepositInMove)?,
            SpendPath::ScriptSpend(0),
            DEFAULT_SEQUENCE,
        )
        .add_output(UnspentTxOut::from_scripts(
            paramset.bridge_amount - paramset.anchor_amount() - EACH_EMERGENCY_STOP_VBYTES * 3,
            vec![Arc::new(Multisig::from_security_council(security_council))],
            None,
            paramset.network,
        ))
        .finalize();

    Ok(builder)
```

**File:** core/src/verifier.rs (L541-601)
```rust
    async fn is_deposit_valid(&self, deposit_data: &mut DepositData) -> Result<(), BridgeError> {
        // check if security council is the same as in our config
        if deposit_data.security_council != self.config.security_council {
            let reason = format!(
                "Security council in deposit is not the same as in the config, expected {:?}, got {:?}",
                self.config.security_council,
                deposit_data.security_council
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }
        // check if extra watchtowers (non verifier watchtowers) are not greater than the maximum allowed
        if deposit_data.actors.watchtowers.len() > MAX_EXTRA_WATCHTOWERS {
            let reason = format!(
                "Number of extra watchtowers in deposit is greater than the maximum allowed, expected at most {}, got {}",
                MAX_EXTRA_WATCHTOWERS,
                deposit_data.actors.watchtowers.len()
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }
        // check if total watchtowers are not greater than the maximum allowed
        if deposit_data.get_num_watchtowers() > MAX_NUMBER_OF_WATCHTOWERS {
            let reason = format!(
                "Number of watchtowers in deposit is greater than the maximum allowed, expected at most {}, got {}",
                MAX_NUMBER_OF_WATCHTOWERS,
                deposit_data.get_num_watchtowers()
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }

        // check if all verifiers are unique
        if !deposit_data.are_all_verifiers_unique() {
            let reason = format!(
                "Verifiers in deposit are not unique: {:?}",
                deposit_data.actors.verifiers
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }

        // check if all watchtowers are unique
        if !deposit_data.are_all_watchtowers_unique() {
            let reason = format!(
                "Watchtowers in deposit are not unique: {:?}",
                deposit_data.actors.watchtowers
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }

        // check if all operators are unique
        if !deposit_data.are_all_operators_unique() {
            let reason = format!(
                "Operators in deposit are not unique: {:?}",
                deposit_data.actors.operators
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }
```
