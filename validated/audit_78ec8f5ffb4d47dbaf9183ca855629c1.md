### Title
Missing `SecurityCouncil.threshold` validation allows trivially-satisfiable move-to-vault script path — (File: `core/src/config/mod.rs`, `core/src/rpc/parser/mod.rs`)

---

### Summary

`BridgeConfig::check_general_requirements()` and the gRPC `TryFrom<clementine::SecurityCouncil>` parser both accept a `SecurityCouncil` with `threshold = 0` (or `threshold > pks.len()`) without error. A zero threshold produces a 0-of-N Bitcoin multisig script that is trivially satisfiable, making the security-council spending path of every move-to-vault UTXO spendable by any party without any signatures. This enables unauthorized replacement-deposit transactions that can redirect bridge-controlled BTC.

---

### Finding Description

**Root cause 1 — config validation gap (`core/src/config/mod.rs`)**

`check_general_requirements()` validates genesis-chain-state hash, start height, and finality depth, but contains no check on the security council: [1](#0-0) 

The fields `security_council.threshold` and `security_council.pks` are never tested for `threshold > 0` or `threshold <= pks.len()`.

**Root cause 2 — gRPC parser gap (`core/src/rpc/parser/mod.rs`)**

`TryFrom<clementine::SecurityCouncil> for SecurityCouncil` validates that each byte slice is a valid `XOnlyPublicKey`, but blindly copies `threshold` without range-checking it: [2](#0-1) 

**How the security council is embedded in every deposit**

In `new_deposit`, the aggregator constructs `DepositData` using `self.config.security_council` and passes it to every verifier: [3](#0-2) 

Each verifier's `is_deposit_valid()` only checks that the deposit's security council *matches* the config — it does not independently validate that `threshold > 0`: [4](#0-3) 

The security council is then baked into the move-to-vault output via `Multisig::from_security_council`, giving every move-to-vault UTXO a script path that requires exactly `threshold` signatures: [5](#0-4) 

**What `threshold = 0` means on-chain**

Bitcoin's `OP_CHECKMULTISIG` with `m = 0` succeeds with only the mandatory dummy stack element. A 0-of-N multisig is unconditionally satisfiable. Any party can construct a witness that passes the security-council script path without holding any key.

---

### Impact Explanation

Every move-to-vault UTXO produced while `threshold = 0` is in the config has a trivially-spendable script path. An attacker can:

1. Construct a `create_replacement_deposit_txhandler` transaction spending the move-to-vault UTXO via the security-council path (zero signatures required).
2. Supply an attacker-controlled `new_nofn_xonly_pk` as the new N-of-N key.
3. The replacement-deposit output inherits the same 0-of-N security-council path, so the attacker can repeat the process or spend the output via the `ReplacementDepositScript` path using the key they control.

This constitutes an unauthorized state transition in the deposit flow and exposes bridge-controlled UTXOs to theft or permanent lock — both within the Allowed Impact Gate.

---

### Likelihood Explanation

The scenario is directly analogous to the external report's "Alice accidentally sets `factoryRegistry` to zero" scenario. An operator who sets `SECURITY_COUNCIL` with `threshold=0` (e.g., during initial setup or a config migration) receives no error from `check_general_requirements()` or `check_mainnet_requirements()`. The misconfiguration silently propagates into every subsequent deposit. The exploit itself requires no privileged access — any on-chain observer can craft the replacement-deposit transaction.

---

### Recommendation

**Short term:** Add the following checks inside `check_general_requirements()` in `core/src/config/mod.rs`:

```rust
if self.security_council.threshold == 0 {
    reasons.push("security_council.threshold must be > 0".to_string());
}
if self.security_council.threshold as usize > self.security_council.pks.len() {
    reasons.push(format!(
        "security_council.threshold ({}) exceeds number of keys ({})",
        self.security_council.threshold,
        self.security_council.pks.len()
    ));
}
if self.security_council.pks.is_empty() {
    reasons.push("security_council.pks must not be empty".to_string());
}
```

Add the same range checks inside `TryFrom<clementine::SecurityCouncil> for SecurityCouncil` in `core/src/rpc/parser/mod.rs` so that a malformed gRPC payload is rejected before it reaches `is_deposit_valid()`.

**Long term:** Mirror the `check_mainnet_requirements()` pattern and add a dedicated `validate_security_council()` helper called from both the config-load path and the gRPC parser, ensuring the invariant is enforced at every entry point.

---

### Proof of Concept

1. Set `SECURITY_COUNCIL` in the aggregator's environment to a value with `threshold = 0` (e.g., two valid public keys, threshold 0).
2. Start the aggregator; `check_general_requirements()` returns `Ok(())` — no error is raised.
3. A user sends BTC to the deposit address and calls `new_deposit`. The aggregator builds `DepositData` with the zero-threshold security council; all verifiers accept it because it matches their config.
4. The move-to-vault transaction is broadcast and confirmed. Its Taproot output contains a 0-of-2 multisig leaf.
5. An attacker constructs a replacement-deposit transaction:
   - Input: the move-to-vault UTXO, script-path spend via the 0-of-2 multisig leaf (witness = `OP_0` dummy only).
   - Output: a new UTXO with `new_nofn_xonly_pk` = attacker's key.
6. The transaction is valid and confirms. The attacker now controls the new UTXO and can spend it via the `ReplacementDepositScript` path using their key, redirecting the bridged BTC. [6](#0-5) [7](#0-6)

### Citations

**File:** core/src/config/mod.rs (L237-289)
```rust
    pub async fn check_general_requirements(&self) -> Result<(), BridgeError> {
        // check genesis state hash
        let rpc = ExtendedBitcoinRpc::connect(
            self.bitcoin_rpc_url.clone(),
            self.bitcoin_rpc_user.clone(),
            self.bitcoin_rpc_password.clone(),
            None,
        )
        .await
        .wrap_err("Failed to connect to Bitcoin RPC while checking general requirements")?;

        let genesis_chain_state = HeaderChainProver::get_chain_state_from_height(
            &rpc,
            self.protocol_paramset().genesis_height.into(),
            self.protocol_paramset().network,
        )
        .await
        .wrap_err("Failed to get genesis chain state while checking general requirements")?;

        let mut reasons = Vec::new();

        if genesis_chain_state.to_hash() != self.protocol_paramset().genesis_chain_state_hash {
            reasons.push(format!(
                "Genesis chain state hash mismatch, state hash generated from Bitcoin RPC ({}) does not match value in config ({})",
                hex::encode(genesis_chain_state.to_hash()),
                hex::encode(self.protocol_paramset().genesis_chain_state_hash)
            ));
        }

        if self.protocol_paramset().start_height < self.protocol_paramset().genesis_height {
            reasons.push(format!(
                "Start height is less than genesis height: {} < {}",
                self.protocol_paramset().start_height,
                self.protocol_paramset().genesis_height
            ));
        }

        if self.protocol_paramset().finality_depth < 1 {
            reasons.push(format!(
                "Finality depth ({}) cannot be less than 1",
                self.protocol_paramset().finality_depth
            ));
        }

        if !reasons.is_empty() {
            return Err(BridgeError::ConfigError(format!(
                "Invalid configuration due to: {}",
                reasons.join(" - ")
            )));
        }

        Ok(())
    }
```

**File:** core/src/rpc/parser/mod.rs (L474-492)
```rust
impl TryFrom<clementine::SecurityCouncil> for SecurityCouncil {
    type Error = Status;

    fn try_from(value: clementine::SecurityCouncil) -> Result<Self, Self::Error> {
        let pks = value
            .pks
            .into_iter()
            .map(|pk| {
                XOnlyPublicKey::from_slice(&pk).map_err(|e| {
                    Status::invalid_argument(format!("Failed to parse xonly public key: {e}"))
                })
            })
            .collect::<Result<Vec<_>, _>>()?;

        Ok(SecurityCouncil {
            pks,
            threshold: value.threshold,
        })
    }
```

**File:** core/src/rpc/aggregator.rs (L1464-1473)
```rust
            let deposit_data = DepositData {
                deposit: deposit_info.clone(),
                nofn_xonly_pk: None,
                actors: Actors {
                    verifiers: self.fetch_verifier_keys().await?,
                    watchtowers: vec![],
                    operators: self.fetch_operator_keys().await?,
                },
                security_council: self.config.security_council.clone(),
            };
```

**File:** core/src/verifier.rs (L542-551)
```rust
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
```

**File:** core/src/builder/transaction/mod.rs (L420-422)
```rust
                    Arc::new(CheckSig::new(old_nofn_xonly_pk)),
                    Arc::new(Multisig::from_security_council(security_council.clone())),
                ],
```
