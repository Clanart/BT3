### Title
Griefing via uneconomical dust outputs scanned as received funds — any party can send marginally-above-dust Taproot outputs to a multisig address which the Scanner reports as spendable inputs, forcing fee burn or unspendable funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The analog of "mint iUSD to an arbitrary victim to impose cost/restriction on them" in Serai's in-scope surface is Bitcoin output dusting: `Scanner::scan_transaction` credits any on-chain output whose `script_pubkey` matches a registered script, and `SignableTransaction::new` accepts every `ReceivedOutput` as an input regardless of whether its value exceeds the marginal fee cost of spending it. An unprivileged attacker can send outputs of value ≥ `DUST` (546 sats, the only threshold anywhere in the pipeline) but below the cost of spending them to the external, change, or forwarded addresses of any multisig — all of which are publicly derivable from the group key.

### Finding Description
`Scanner::scan_transaction` (networks/bitcoin/src/wallet/mod.rs:199-214) performs matching purely on `output.script_pubkey` with no economic check; the only value filter in the entire pipeline is `output.balance().amount.0 >= N::DUST` in the processor's scanner, and `DUST = 546` (networks/bitcoin/src/wallet/send.rs:32). The scripts scanned are deterministic functions of the group key: external (`Scalar::ZERO`), plus `Branch`, `Change`, and `Forwarded` offsets derived as `Secp256k1::hash_to_F(b"Serai Bitcoin Output Offset", ...)` (processor/src/networks/bitcoin.rs:308-346), so any observer of the multisig's transactions can compute every scannable script pubkey.

A key-p2tr input adds ~58 vbytes to a transaction. At any fee rate above ~9.4 sat/vB, spending a 546-sat output costs more than its value — and at typical congestion-era rates (50+ sat/vB) even outputs of several thousand sats are net-negative. `SignableTransaction::new` (send.rs:150-256) charges `needed_fee = fee_per_vbyte * vbytes` where vbytes scales with `tx_ins.len()` (send.rs:204-206), and only verifies aggregate `input_sat >= payment_sat + needed_fee` (send.rs:215). It never checks that any individual input is worth more than its marginal fee contribution. Every scanned dust output therefore either (a) gets included as an input, burning multisig funds in fees exceeding the dust's value, or (b) is excluded by integrator logic the library does not provide, leaving funds that were reported received but are economically unspendable.

Additionally, each input requires a full FROST signing round: `multisig` creates one `AlgorithmMachine` per input (send.rs:273-285) and `sign` produces a per-input Schnorr signature share (send.rs:378-397). Flooding a multisig with small outputs inflates every subsequent spend's signature count, preprocessing bandwidth, and on-chain weight — a direct griefing tax on the victim multisig payable by the attacker at ~546 sats per unit.

### Impact Explanation
An unprivileged attacker with only the ability to send Bitcoin transactions can: (1) force the multisig to pay fees exceeding the value of attacker-deposited outputs whenever those outputs are spent — a slow, repeatable drain on protocol funds; (2) create outputs that are reported as received balance yet are not economically spendable at any reasonable fee rate, permanently locking accounting value; (3) inflate input counts to force larger, more expensive signing sessions, and in the extreme push transactions toward `MAX_STANDARD_TX_WEIGHT` (send.rs:241), degrading to a signing DoS until the dust is isolated. Like the iUSD finding, the attacker's cost per griefing unit is minimal (~546 sats) while the victim's cost (fee burn + coordination rounds) is multiplicative.

### Likelihood Explanation
The required scripts are publicly computable from the group key and visible on-chain after the multisig's first transaction, so no privileged position is needed. Dusting is a well-known, cheap, and frequently executed attack pattern against Bitcoin wallets and bridges. The trigger condition — attacker sends a normal, valid transaction — is trivially satisfiable at any time.

### Recommendation
Introduce an economic-viability floor in or alongside `Scanner`/`SignableTransaction`: define a minimum input value relative to the current fee rate (e.g., reject or quarantine outputs where `value < fee_per_vbyte * INPUT_VBYTES`), rather than the static 546-sat `DUST` constant, which is a relay-policy limit, not a profitability bound. Alternatively, track dust outputs separately so integrators can batch-sweep them only when fee rates make it profitable, and never merge attacker-controlled dust into payment transactions.

### Proof of Concept
1. Observe any spend from a Serai Bitcoin multisig to learn its group key `K` (or compute `K`'s external/change/forward scripts directly via `p2tr_script_buf` and the public `hash_to_F` offsets in processor/src/networks/bitcoin.rs:308-346).
2. During a high-fee-rate period (e.g., 100 sat/vB), broadcast transactions creating N outputs of exactly 546 sats each to the multisig's external script.
3. `Scanner::scan_transaction` returns all N as `ReceivedOutput`s; the processor's `>= N::DUST` filter accepts them since 546 ≥ 546.
4. Any `SignableTransaction` including these inputs adds N × ~58 vbytes of weight; at 100 sat/vB each input costs ~5,800 sats in fees for 546 sats of value — a net burn of ~5,254 sats per output, plus one extra FROST signing round per input. Spending is mandatory to consolidate or retire the multisig, so the cost cannot be avoided indefinitely. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** networks/bitcoin/src/wallet/send.rs (L204-215)
```rust
    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

    let mut needed_fee = fee_per_vbyte * vbytes;
    // Technically, if there isn't change, this TX may still pay enough of a fee to pass the
    // minimum fee. Such edge cases aren't worth programming when they go against intent, as the
    // specified fee rate is too low to be valid
    // bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE is in sats/kilo-vbyte
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
    }

    if input_sat < (payment_sat + needed_fee) {
```

**File:** networks/bitcoin/src/wallet/send.rs (L273-285)
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
  }
```

**File:** processor/src/networks/bitcoin.rs (L308-346)
```rust
const KEY_DST: &[u8] = b"Serai Bitcoin Output Offset";
static BRANCH_OFFSET: OnceLock<Scalar> = OnceLock::new();
static CHANGE_OFFSET: OnceLock<Scalar> = OnceLock::new();
static FORWARD_OFFSET: OnceLock<Scalar> = OnceLock::new();

// Always construct the full scanner in order to ensure there's no collisions
fn scanner(
  key: ProjectivePoint,
) -> (Scanner, HashMap<OutputType, Scalar>, HashMap<Vec<u8>, OutputType>) {
  let mut scanner = Scanner::new(key).unwrap();
  let mut offsets = HashMap::from([(OutputType::External, Scalar::ZERO)]);

  let zero = Scalar::ZERO.to_repr();
  let zero_ref: &[u8] = zero.as_ref();
  let mut kinds = HashMap::from([(zero_ref.to_vec(), OutputType::External)]);

  let mut register = |kind, offset| {
    let offset = scanner.register_offset(offset).expect("offset collision");
    offsets.insert(kind, offset);

    let offset = offset.to_repr();
    let offset_ref: &[u8] = offset.as_ref();
    kinds.insert(offset_ref.to_vec(), kind);
  };

  register(
    OutputType::Branch,
    *BRANCH_OFFSET.get_or_init(|| Secp256k1::hash_to_F(KEY_DST, b"branch")),
  );
  register(
    OutputType::Change,
    *CHANGE_OFFSET.get_or_init(|| Secp256k1::hash_to_F(KEY_DST, b"change")),
  );
  register(
    OutputType::Forwarded,
    *FORWARD_OFFSET.get_or_init(|| Secp256k1::hash_to_F(KEY_DST, b"forward")),
  );

  (scanner, offsets, kinds)
```
