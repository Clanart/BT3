### Title
Scanner reports zero/dust-valued outputs as received funds that are economically unspendable - (networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_transaction` and `Scanner::scan_block` return a `ReceivedOutput` for every output whose `script_pubkey` matches a registered script, with no check on the output's value. An unprivileged party can send a zero-value or below-`DUST` (546 sat) output to a registered offset script, and the wallet will report it as a received output even though spending it yields nothing (or costs more in fees than it contributes). This is the Serai analog of "deposit accepted while minting 0 shares": the input is consumed/credited while producing zero usable value.

### Finding Description
In `networks/bitcoin/src/wallet/mod.rs:199-214`, `scan_transaction` iterates all outputs and pushes a `ReceivedOutput` whenever `self.scripts.get(&output.script_pubkey)` hits, copying `output.clone()` verbatim — `value` is never validated:

```rust
if let Some(offset) = self.scripts.get(&output.script_pubkey) {
  res.push(ReceivedOutput { offset: *offset, output: output.clone(), ... });
}
``` [1](#0-0) 

Downstream, `SignableTransaction::new` in `networks/bitcoin/src/wallet/send.rs` treats every supplied `ReceivedOutput` as a spendable input and only enforces `DUST` on *payments*, not on *inputs* (`send.rs:165-169`). Each Taproot input adds ~58 vbytes to the transaction, so `needed_fee = fee_per_vbyte * vbytes` (`send.rs:204-206`) grows by more than the value of a dust input. A 0-value or sub-dust input therefore contributes literally nothing (or net-negative value) while still being spent — the output is consumed forever with zero return, exactly the "999 assets in, 0 shares out" shape. Additionally, when leftover change is below `DUST`, `SignableTransaction::new` silently omits the change output and burns the remainder as fee (`send.rs:224-234`), compounding the silent value loss.

### Impact Explanation
Funds are reported received that are not spendable: an external sender can create outputs to the multisig's script_pubkey (including outputs under offsets registered for other purposes) with dust or zero value. The scanner credits them via `ReceivedOutput::value()` (`mod.rs:116-118`), and consuming them either returns nothing or destroys additional value from other inputs through fee increase. Since these outputs are recorded at face value but are economically worthless, the reported balance overstates spendable funds — a loss-of-funds / false-credit primitive reachable purely with a Bitcoin transaction an unprivileged party sends.

### Likelihood Explanation
Any Bitcoin user can send a transaction creating a small output to a known P2TR script_pubkey (the multisig key's script is public on-chain, and `register_offset` scripts are deterministically derivable). No validator cooperation, no protocol messages, and no special access are required — the attack surface is just an on-chain transaction, matching the "Bitcoin transactions they send" reachability rule. The cost to the attacker is the dust amount itself, which can be 0 or a few hundred sats.

### Recommendation
Enforce a minimum value on scanned outputs: skip (or flag) outputs in `scan_transaction`/`scan_block` with `output.value.to_sat() < DUST`, or record them distinctly so callers never schedule them as inputs. In `SignableTransaction::new`, reject or filter inputs whose value is below `DUST`, and treat inputs whose value is below their marginal fee contribution (`fee_per_vbyte * input_vbytes`) as unspendable rather than silently consuming them.

### Proof of Concept
1. Attacker learns the multisig's `p2tr_script_buf(key)` (public from any prior deposit).
2. Attacker broadcasts a transaction containing `TxOut { value: Amount::from_sat(0..=545), script_pubkey: <multisig script> }`.
3. `Scanner::scan_transaction` returns a `ReceivedOutput` for it (`mod.rs:205-211`), and `output.value()` reports it as received funds.
4. When the wallet later calls `SignableTransaction::new` including this input, `needed_fee` increases by `fee_per_vbyte * ~58 vbytes` (`send.rs:204-206`) while `input_sat` grows by at most 545 sats — the input is consumed, yields zero or negative net value, and is permanently destroyed, mirroring a deposit that mints 0 shares.

Note: severity hinges on whether callers credit scanned outputs at face value without a dust floor; that crediting logic lives in the (out-of-scope) processor, but the missing minimum-value check at the in-scope scanning/boundary layer is the root cause.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L199-214)
```rust
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
