### Title
Immature coinbase outputs are reported as spendable vault funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` treats the block’s coinbase transaction like every other transaction and returns any output matching a registered vault script as a spendable `ReceivedOutput`. Because the scanner does not retain maturity metadata or exclude `block.txdata[0]`, downstream transaction construction can count and attempt to spend an output that Bitcoin consensus rejects until 100 blocks have elapsed. This is a **Medium** availability/accounting vulnerability.

### Finding Description
`ReceivedOutput` is defined as “a spendable output,” and `scan_transaction` identifies ownership solely by matching `output.script_pubkey` against the scanner’s registered scripts. [1](#0-0) [2](#0-1) 

`scan_block` then applies that logic to every transaction in `block.txdata`, including index `0`, which is always the coinbase transaction in a valid block. [3](#0-2) 

The resulting `ReceivedOutput` carries only an offset, `TxOut`, and outpoint; it does not indicate whether the output came from a coinbase or when it becomes mature. [4](#0-3) 

`SignableTransaction::new` subsequently sums every supplied output’s value into `input_sat` and creates a transaction input for every supplied outpoint without checking coinbase maturity. [5](#0-4) 

### Impact Explanation
A miner can create a block whose coinbase pays a Serai vault’s Taproot script. The scanner will report that coinbase output as received and spendable even though Bitcoin consensus prohibits spending it for 100 blocks.

If downstream scheduling uses that output as available vault balance, it may sign a withdrawal transaction that references the immature coinbase output. Bitcoin nodes will reject the transaction as a premature coinbase spend, leaving the requested withdrawal unavailable until the transaction is rescheduled with different inputs. If the immature output is needed to satisfy the requested payment amount, the wallet has effectively over-reported spendable vault funds.

### Likelihood Explanation
Any miner can place a vault script in a coinbase output using only public blockchain data. No validator key share, malicious peer, compromised RPC, or malformed private state is required; the hostile input is simply a valid Bitcoin block processed by `scan_block`.

The issue requires the resulting output to be selected before maturity. Since normal deposit confirmation depths can be lower than Bitcoin’s 100-block coinbase maturity, a coinbase deposit can pass ordinary confirmation handling while still being consensus-unspendable.

### Recommendation
`scan_block` should not return coinbase outputs as immediately spendable `ReceivedOutput`s. At minimum, skip `block.txdata[0]`; alternatively, return coinbase outputs through a type that records their creation height and prohibits selection until at least 100 confirmations have elapsed.

A defense-in-depth check should also reject coinbase outpoints during withdrawal planning or transaction construction when the current chain height is less than the required maturity height.

### Proof of Concept
Conceptually, using a valid regtest block whose coinbase pays the vault:

```rust
let mut scanner = Scanner::new(vault_key).unwrap();
let vault_script = p2tr_script_buf(vault_key).unwrap();

// valid_block.txdata[0] is a coinbase transaction with an output whose
// script_pubkey equals vault_script.
let outputs = scanner.scan_block(&valid_block);

// The coinbase output is returned even though it cannot be spent for 100 blocks.
assert_eq!(outputs.len(), 1);
let immature = outputs[0].clone();

// Transaction construction counts its value and creates an input for it.
let signable = SignableTransaction::new(
    vec![immature],
    &[(payment_script, payment_amount)],
    change_script,
    None,
    fee_per_vbyte,
)?;

// After threshold signing, Bitcoin consensus rejects the transaction as a
// premature coinbase spend.
```

The scanner will include the output solely because its script matches the registered vault script. [6](#0-5)  The transaction builder then trusts the reported value as part of `input_sat` and spends the associated outpoint without checking maturity. [5](#0-4)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L88-97)
```rust
/// A spendable output.
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct ReceivedOutput {
  // The scalar offset to obtain the key usable to spend this output.
  offset: Scalar,
  // The output to spend.
  output: TxOut,
  // The TX ID and vout of the output to spend.
  outpoint: OutPoint,
}
```

**File:** networks/bitcoin/src/wallet/mod.rs (L198-212)
```rust
  /// Scan a transaction.
  pub fn scan_transaction(&self, tx: &Transaction) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for (vout, output) in tx.output.iter().enumerate() {
      // If the vout index exceeds 2**32, stop scanning outputs
      let Ok(vout) = u32::try_from(vout) else { break };

      if let Some(offset) = self.scripts.get(&output.script_pubkey) {
        res.push(ReceivedOutput {
          offset: *offset,
          output: output.clone(),
          outpoint: OutPoint::new(tx.compute_txid(), vout),
        });
      }
    }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L221-227)
```rust
  pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for tx in &block.txdata {
      res.extend(self.scan_transaction(tx));
    }
    res
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-185)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
    let tx_ins = inputs
      .iter()
      .map(|input| TxIn {
        previous_output: input.outpoint,
        script_sig: ScriptBuf::new(),
        sequence: Sequence::MAX,
        witness: Witness::new(),
      })
      .collect::<Vec<_>>();
```
