### Title
Serialized `ReceivedOutput` can claim unverified wallet ownership and funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` deserializes the spending offset, `TxOut`, and claimed `OutPoint` without verifying that the output exists on-chain or that the declared offset actually authorizes the output for the wallet. An attacker who can provide serialized `ReceivedOutput` bytes can therefore cause an application to report an arbitrary payment as received and spendable even though no corresponding UTXO exists.

### Finding Description
`ReceivedOutput::read` accepts an arbitrary scalar offset, transaction output, and outpoint directly from the input stream, then constructs the spendable-output representation without validating any relationship between those fields or validating the claimed outpoint against chain state. [1](#0-0) 

The exposed `value`, `output`, `outpoint`, and `offset` accessors all return these attacker-controlled fields. [2](#0-1) 

`SignableTransaction::new` subsequently trusts the serialized output value as input balance and trusts the serialized outpoint as the transaction's previous-output reference. [3](#0-2) 

The only wallet-authorization check occurs later in `SignableTransaction::multisig`, which verifies that the stored script corresponds to `keys.offset(offset).group_key()`. [4](#0-3)  It does not verify that the output exists, is unspent, or was actually discovered by `Scanner::scan_transaction`. [5](#0-4) 

### Impact Explanation
An attacker can fabricate a `ReceivedOutput` naming a wallet-controlled P2TR script, an arbitrary value, and a nonexistent or unrelated `OutPoint`. Any application treating the deserialized object as an authenticated scan result will credit funds that cannot be spent and may include the fake input in transaction construction. The result is incorrect balance reporting and signing attempts for transactions Bitcoin consensus can never accept.

This is analogous to the reported authorization failure: bytes describing another resource are accepted without checking that the requester or serialized credential has authority over that resource. Here, the resource is a wallet output, and the missing authorization check is the absent binding between `offset`, `script_pubkey`, `OutPoint`, and chain-confirmed ownership.

### Likelihood Explanation
Exploitation requires an integration to deserialize attacker-supplied `ReceivedOutput` values rather than exclusively constructing them through `Scanner::scan_transaction` or `Scanner::scan_block`. The public `read`, `serialize`, and `write` APIs make such propagation plausible. [6](#0-5)  The attack does not require knowledge of the wallet's private key because a wallet-controlled script can be copied and paired with an arbitrary outpoint and amount.

### Recommendation
Treat `ReceivedOutput` as an authenticated scanner result rather than a self-authorizing serializable credential.

At minimum:

1. Add a verification method taking the wallet's base `ProjectivePoint` and checking:
   - `output.script_pubkey == p2tr_script_buf(base_key + GENERATOR * offset)`;
   - the claimed `OutPoint` resolves on-chain to exactly `output`;
   - the output is confirmed, unspent, and not subject to coinbase maturity restrictions.
2. Move this verification into deserialization call sites that consume untrusted data.
3. Prefer serializing an authenticated scanner record that commits to the scanner identity or group key, rather than serializing only `offset || TxOut || OutPoint`.
4. Reject `ReceivedOutput` values whose script is not P2TR or whose derived key cannot produce the serialized script.

### Proof of Concept
Conceptually, for wallet base key `K`:

```text
offset   = Scalar::ZERO
txout    = TxOut {
             value: attacker_chosen_large_amount,
             script_pubkey: p2tr_script_buf(K).unwrap(),
           }
outpoint = OutPoint {
             txid: txid_of_an_unrelated_or_nonexistent_transaction,
             vout: chosen_vout,
           }
bytes    = offset.to_repr() || consensus_encode(txout) ||
           consensus_encode(outpoint)
```

Feed `bytes` to `ReceivedOutput::read`. Deserialization succeeds even though no such wallet UTXO need exist. [7](#0-6) 

Passing the result to `SignableTransaction::new` causes its fake value and outpoint to be used as transaction input data. [3](#0-2)  `multisig` accepts the ownership check when `offset = 0` and the fabricated script is the wallet's P2TR script, but the resulting transaction still spends a nonexistent or unrelated previous output. [8](#0-7)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L99-118)
```rust
impl ReceivedOutput {
  /// The offset for this output.
  pub fn offset(&self) -> Scalar {
    self.offset
  }

  /// The Bitcoin output for this output.
  pub fn output(&self) -> &TxOut {
    &self.output
  }

  /// The outpoint for this output.
  pub fn outpoint(&self) -> &OutPoint {
    &self.outpoint
  }

  /// The value of this output.
  pub fn value(&self) -> u64 {
    self.output.value.to_sat()
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L120-148)
```rust
  /// Read a ReceivedOutput from a generic satisfying Read.
  #[cfg(feature = "std")]
  pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
    let offset = Secp256k1::read_F(r)?;
    let output;
    let outpoint;
    {
      let mut buf_r = BufReader::with_capacity(0, r);
      output =
        TxOut::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid TxOut"))?;
      outpoint =
        OutPoint::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid OutPoint"))?;
    }
    Ok(ReceivedOutput { offset, output, outpoint })
  }

  /// Write a ReceivedOutput to a generic satisfying Write.
  pub fn write<W: Write>(&self, w: &mut W) -> io::Result<()> {
    w.write_all(&self.offset.to_bytes())?;
    w.write_all(&serialize(&self.output))?;
    w.write_all(&serialize(&self.outpoint))
  }

  /// Serialize a ReceivedOutput to a `Vec<u8>`.
  pub fn serialize(&self) -> Vec<u8> {
    let mut res = Vec::new();
    self.write(&mut res).unwrap();
    res
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L198-214)
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

**File:** networks/bitcoin/src/wallet/send.rs (L273-284)
```rust
  pub fn multisig(self, keys: &ThresholdKeys<Secp256k1>) -> Option<TransactionMachine> {
    let mut sigs = vec![];
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }

      sigs.push(AlgorithmMachine::new(Schnorr::new(), keys.clone().offset(self.offsets[i])));
    }

    Some(TransactionMachine { tx: self, sigs })
```
