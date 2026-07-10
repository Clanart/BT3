### Title
Fixed 30 TGas Compute Budget for EVM Receipt Proof Verification Can Permanently Lock User Funds - (File: near/omni-bridge/src/lib.rs, near/omni-prover/evm-prover/src/lib.rs)

### Summary
The Omni Bridge allocates a hard-coded 30 TGas budget when dispatching to the `evm-prover`'s `verify_proof` function. Inside that function, the actual compute available for proof work is only ~20 TGas after reserving sub-call budgets. The proof inputs (`receipt_data`, `header_data`, `proof` nodes) have no size limits, and the trie-traversal is recursive. For EVM transactions included in high-traffic blocks (deep Patricia trie) or transactions whose receipts contain many logs, the compute budget can be exhausted before verification completes, causing the cross-chain transfer to fail permanently with no recovery path.

### Finding Description

The bridge's internal `verify_proof` helper dispatches to the registered prover with a fixed static gas allocation:

```rust
const VERIFY_PROOF_GAS: Gas = Gas::from_tgas(30);
// ...
ext_omni_prover_proxy::ext(prover_account_id)
    .with_static_gas(VERIFY_PROOF_GAS)
    .verify_proof(prover_args)
``` [1](#0-0) [2](#0-1) 

Inside `evm-prover::verify_proof`, two further sub-calls are scheduled with static gas:

```rust
const VERIFY_PROOF_CALLBACK_GAS: Gas = Gas::from_tgas(5);
const BLOCK_HASH_SAFE_GAS: Gas = Gas::from_tgas(5);
``` [3](#0-2) 

This leaves at most **20 TGas** for the actual proof computation: RLP-decoding the block header, log entry, and receipt, plus the recursive Patricia trie traversal. None of the proof input fields carry any size bound:

```rust
pub struct EvmProof {
    pub log_entry_data: Vec<u8>,   // unbounded
    pub receipt_data: Vec<u8>,     // unbounded
    pub header_data: Vec<u8>,      // unbounded
    pub proof: Vec<Vec<u8>>,       // unbounded depth
}
``` [4](#0-3) 

The trie traversal is recursive with no depth guard:

```rust
fn _verify_trie_proof(
    expected_root: Vec<u8>,
    key: &Vec<u8>,
    proof: &Vec<Vec<u8>>,
    key_index: usize,
    proof_index: usize,
) -> Vec<u8> {
    // ... recursive calls at proof_index + 1
``` [5](#0-4) 

Each trie node requires a `keccak256` call (~1 TGas on NEAR). A block containing ~1 000 transactions produces a trie of depth ~10, consuming ~10 TGas for hashing alone. A receipt with many logs (e.g., a bridge call routed through a DeFi aggregator) adds further RLP-decode cost. Together these can exhaust the 20 TGas compute window.

The `Receipt` decoder unconditionally decodes **all** logs in the receipt before the bridge can access the single target log:

```rust
let receipt: Receipt = rlp::decode(&evm_proof.receipt_data).map_err(|e| e.to_string())?;
// ...
require!(receipt.logs[log_index_usize] == log_entry);
``` [6](#0-5) 

### Impact Explanation

When `verify_proof` exhausts its 30 TGas budget, the NEAR runtime panics the call. The `fin_transfer_callback` receives a `PromiseError` and panics with `BridgeError::InvalidProofMessage`. The user's tokens are already locked or burned on the EVM side (the `InitTransfer` event was emitted), but the NEAR-side finalization can never succeed because the gas cap is baked into the contract constant and cannot be overridden by the relayer. There is no retry mechanism or escape hatch in the bridge for this failure mode. The result is **permanent, irrecoverable lock of user funds**. [7](#0-6) [8](#0-7) 

### Likelihood Explanation

The trigger requires a legitimate EVM `InitTransfer` transaction to be included in a high-traffic Ethereum block (deep trie) or to be part of a complex call that emits many logs in the same receipt. Both conditions occur naturally on mainnet during periods of congestion. The user cannot avoid them: block inclusion is non-deterministic, and the trie depth is determined by the block's transaction count, not the user's transaction. No malicious intent is required; any sufficiently large legitimate proof silently fails.

### Recommendation

1. **Benchmark** the NEAR compute cost of `verify_proof` across a range of realistic Ethereum blocks (varying transaction counts and receipt sizes) to establish a safe minimum gas budget.
2. **Raise `VERIFY_PROOF_GAS`** to a value that accommodates worst-case realistic proofs, or make it configurable by the DAO.
3. **Add size guards** on `EvmProof` fields (maximum `proof` depth, maximum `receipt_data` length) so oversized inputs are rejected early with a clear error rather than silently exhausting gas.
4. **Add a depth limit** to `_verify_trie_proof` to prevent unbounded recursion.
5. **Provide a recovery path** (e.g., an admin function to manually finalize or refund a transfer whose proof verification failed) so users are not permanently locked out.

### Proof of Concept

1. User calls `initTransfer` on the EVM bridge during a high-congestion block (e.g., 2 000+ transactions). The transaction is included; the `InitTransfer` log is emitted.
2. The relayer constructs the `EvmProof`: the Patricia trie proof has ~11 nodes, each ~500 bytes; the receipt contains 15 logs from a DeFi aggregator wrapper.
3. The relayer calls `fin_transfer` on the NEAR bridge. The bridge dispatches `verify_proof` with `VERIFY_PROOF_GAS = 30 TGas`.
4. Inside `evm-prover::verify_proof`, RLP-decoding the 15-log receipt and traversing 11 trie nodes (11 × keccak256 ≈ 11 TGas, plus RLP overhead) exhausts the ~20 TGas compute window before `block_hash_safe` is even called.
5. NEAR panics the function. `fin_transfer_callback` receives `PromiseError`; the bridge panics with `InvalidProofMessage`.
6. The user's EVM tokens are locked; no NEAR tokens are minted; no refund mechanism exists. Funds are permanently frozen. [1](#0-0) [9](#0-8) [10](#0-9) [11](#0-10)

### Citations

**File:** near/omni-bridge/src/lib.rs (L82-82)
```rust
const VERIFY_PROOF_GAS: Gas = Gas::from_tgas(30);
```

**File:** near/omni-bridge/src/lib.rs (L673-695)
```rust
    pub fn fin_transfer(&mut self, #[serializer(borsh)] args: FinTransferArgs) -> Promise {
        require!(
            args.storage_deposit_actions.len() <= 3,
            BridgeError::InvalidStorageAccountsLen.as_ref()
        );
        let mut main_promise = self.verify_proof(args.chain_kind, args.prover_args);

        let mut attached_deposit = env::attached_deposit();

        for action in &args.storage_deposit_actions {
            main_promise =
                main_promise.and(Self::check_or_pay_ft_storage(action, &mut attached_deposit));
        }

        main_promise.then(
            Self::ext(env::current_account_id())
                .with_attached_deposit(attached_deposit)
                .with_static_gas(FIN_TRANSFER_CALLBACK_GAS)
                .fin_transfer_callback(
                    &args.storage_deposit_actions,
                    env::predecessor_account_id(),
                ),
        )
```

**File:** near/omni-bridge/src/lib.rs (L704-707)
```rust
    ) -> PromiseOrValue<Nonce> {
        let Ok(ProverResult::InitTransfer(init_transfer)) = Self::decode_prover_result(0) else {
            env::panic_str(BridgeError::InvalidProofMessage.to_string().as_str())
        };
```

**File:** near/omni-bridge/src/lib.rs (L2764-2767)
```rust
        ext_omni_prover_proxy::ext(prover_account_id)
            .with_static_gas(VERIFY_PROOF_GAS)
            .with_attached_deposit(NearToken::from_near(0))
            .verify_proof(prover_args)
```

**File:** near/omni-prover/evm-prover/src/lib.rs (L16-17)
```rust
const VERIFY_PROOF_CALLBACK_GAS: Gas = Gas::from_tgas(5);
const BLOCK_HASH_SAFE_GAS: Gas = Gas::from_tgas(5);
```

**File:** near/omni-prover/evm-prover/src/lib.rs (L59-99)
```rust
    pub fn verify_proof(&self, #[serializer(borsh)] input: Vec<u8>) -> Result<Promise, String> {
        let args = EvmVerifyProofArgs::try_from_slice(&input)
            .map_err(|_| ProverError::ParseArgs.to_string())?;

        let evm_proof = args.proof;
        let header: BlockHeader = rlp::decode(&evm_proof.header_data).map_err(|e| e.to_string())?;
        let log_entry: LogEntry =
            rlp::decode(&evm_proof.log_entry_data).map_err(|e| e.to_string())?;
        let receipt: Receipt = rlp::decode(&evm_proof.receipt_data).map_err(|e| e.to_string())?;

        // Verify log_entry included in receipt
        let log_index_usize = usize::try_from(evm_proof.log_index).map_err(|e| e.to_string())?;
        require!(receipt.logs[log_index_usize] == log_entry);

        // Verify receipt included into header
        let data = Self::verify_trie_proof(
            header.receipts_root.0,
            rlp::encode(&evm_proof.receipt_index).to_vec(),
            &evm_proof.proof,
        );

        if evm_proof.receipt_data != data {
            return Err(ProverError::InvalidProof.to_string());
        }

        // Verify block header was in the bridge
        Ok(evm_client::ext(self.light_client.clone())
            .with_static_gas(BLOCK_HASH_SAFE_GAS)
            .block_hash_safe(header.number.as_u64())
            .then(
                Self::ext(env::current_account_id())
                    .with_static_gas(VERIFY_PROOF_CALLBACK_GAS)
                    .verify_proof_callback(
                        args.proof_kind,
                        evm_proof.log_entry_data,
                        header
                            .hash
                            .ok_or_else(|| ProverError::HashNotSet.to_string())?
                            .0,
                    ),
            ))
```

**File:** near/omni-prover/evm-prover/src/lib.rs (L139-146)
```rust
    fn verify_trie_proof(expected_root: H256, key: Vec<u8>, proof: &Vec<Vec<u8>>) -> Vec<u8> {
        let mut actual_key = vec![];
        for el in key {
            actual_key.push(el / 16);
            actual_key.push(el % 16);
        }
        Self::_verify_trie_proof(expected_root.to_vec(), &actual_key, proof, 0, 0)
    }
```

**File:** near/omni-prover/evm-prover/src/lib.rs (L149-232)
```rust
    fn _verify_trie_proof(
        expected_root: Vec<u8>,
        key: &Vec<u8>,
        proof: &Vec<Vec<u8>>,
        key_index: usize,
        proof_index: usize,
    ) -> Vec<u8> {
        let node = &proof[proof_index];

        if key_index == 0 {
            // trie root is always a hash
            require!(keccak256(node) == expected_root.as_slice());
        } else if node.len() < 32 {
            // if rlp < 32 bytes, then it is not hashed
            require!(node.as_slice() == expected_root);
        } else {
            require!(keccak256(node) == expected_root.as_slice());
        }

        let node = Rlp::new(node.as_slice());

        if node.iter().count() == 17 {
            // Branch node
            if key_index >= key.len() {
                require!(proof_index + 1 == proof.len());
                get_vec(&node, 16)
            } else {
                #[allow(clippy::as_conversions)]
                let new_expected_root = get_vec(&node, key[key_index] as usize);
                if new_expected_root.is_empty() {
                    // not included in proof
                    vec![]
                } else {
                    Self::_verify_trie_proof(
                        new_expected_root,
                        key,
                        proof,
                        key_index + 1,
                        proof_index + 1,
                    )
                }
            }
        } else {
            // Leaf or extension node
            require!(node.iter().count() == 2);
            let path_u8 = get_vec(&node, 0);
            // Extract first nibble
            let head = path_u8[0] / 16;
            // require!(0 <= head); is implicit because of type limits
            require!(head <= 3);

            // Extract path
            let mut path = vec![];
            if head % 2 == 1 {
                path.push(path_u8[0] % 16);
            }
            for val in path_u8.iter().skip(1) {
                path.push(val / 16);
                path.push(val % 16);
            }

            if head >= 2 {
                // Leaf node
                require!(proof_index + 1 == proof.len());
                require!(key_index + path.len() == key.len());
                if path.as_slice() == &key[key_index..key_index + path.len()] {
                    get_vec(&node, 1)
                } else {
                    vec![]
                }
            } else {
                // Extension node
                require!(path.as_slice() == &key[key_index..key_index + path.len()]);
                let new_expected_root = get_vec(&node, 1);
                Self::_verify_trie_proof(
                    new_expected_root,
                    key,
                    proof,
                    key_index + path.len(),
                    proof_index + 1,
                )
            }
        }
    }
```

**File:** near/omni-types/src/prover_args.rs (L26-35)
```rust
#[near(serializers=[borsh, json])]
#[derive(Default, Debug, Clone)]
pub struct EvmProof {
    pub log_index: u64,
    pub log_entry_data: Vec<u8>,
    pub receipt_index: u64,
    pub receipt_data: Vec<u8>,
    pub header_data: Vec<u8>,
    pub proof: Vec<Vec<u8>>,
}
```
