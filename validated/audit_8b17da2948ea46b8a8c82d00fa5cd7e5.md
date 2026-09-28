### Title
`SignableTransaction::new` signs payments to provably unspendable / non-standard `script_pubkey`s, permanently burning or freezing multisig funds - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The original report's bug class is "an asset is transferred to a destination that cannot actually receive or spend it, so it is stuck forever." The Serai analog lives in `SignableTransaction::new` in `networks/bitcoin/src/wallet/send.rs:150-256`. Payment destinations are accepted as raw `ScriptBuf`s and are only validated for amount (`>= DUST` at line 165-169). There is no check that the destination script is spendable or even standard, so the FROST multisig will produce a fully signed transaction paying arbitrary amounts to a provably unspendable output (e.g., a script beginning with `OP_RETURN`/`OP_FALSE`), burning those funds forever — the Bitcoin equivalent of an NFT transferred to a contract that can't handle ERC721.

### Finding Description
`SignableTransaction::new` takes `payments: &[(ScriptBuf, u64)]` and copies each entry verbatim into `tx_outs` (`send.rs:187-191`). The only per-payment validation is the dust check:

```rust
for (_, amount) in payments {
  if *amount < DUST {
    Err(TransactionError::DustPayment)?;
  }
}
```

Notably, the code itself knows `OP_RETURN` outputs must carry zero value — the library's own `data` field is emitted as `TxOut { value: Amount::ZERO, script_pubkey: ScriptBuf::new_op_return(...) }` at `send.rs:194-202`. Yet nothing prevents a payment `ScriptBuf` from being `OP_RETURN`-prefixed (provably unspendable, burns `amount`) or otherwise non-standard/invalid (e.g., malformed push opcodes, >520-byte push semantics, `OP_RETURN` embedded mid-script). Consensus accepts any `script_pubkey` bytes, so:

- An unspendable-but-standard script gets mined → funds permanently burned.
- A non-standard script makes the entire transaction non-relayable → the whole batched transaction (all inputs, all other users' payments, change) fails to propagate, freezing the plan.

The transaction is then signed in `TransactionSignMachine::sign` (`send.rs:373-397`) and completed in `TransactionSignatureMachine::complete` (`send.rs:413-428`) without ever inspecting output scripts — `multisig()` (`send.rs:273-285`) only verifies that *prevout* scripts match the group's keys, never that *payment* destinations are spendable.

In Serai's real shape, these payment scripts are derived from user-supplied withdrawal addresses (an unprivileged party requesting an OutInstruction supplies the destination `Address`, which for Bitcoin wraps a `ScriptBuf`). The multisig therefore signs a transaction that irrevocably destroys funds based on attacker/self-supplied destination bytes — directly analogous to `Vault.sol` sending an NFT to a recipient that cannot receive it.

### Impact Explanation
Any withdrawal request can name a provably unspendable `script_pubkey`. Once the threshold signs and the transaction is mined, the funds are permanently unrecoverable — identical to the source report's "NFTs stuck forever." If the script is merely non-standard rather than unspendable, the entire signed transaction cannot be relayed through the default mempool, freezing all inputs and co-batched payments in that `SignableTransaction`. This maps to the accepted impact class of funds being committed to outputs that are not spendable.

### Likelihood Explanation
The payment list is populated from externally supplied destinations (withdrawal instructions carry arbitrary `Address`/`ScriptBuf` values), so reaching the path only requires submitting a withdrawal to a malformed or `OP_RETURN`-style script — either maliciously or by accident (e.g., a buggy address encoder on the user side, the same "user sets a bad recipient" scenario as the original report). No threshold compromise or validator misbehavior is needed; the library itself lacks the guard.

### Recommendation
In `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:150`), validate each payment `ScriptBuf` before building `tx_outs`:

- Reject scripts that are provably unspendable (`script.is_provably_unspendable()` — i.e., first opcode is `OP_RETURN` or `OP_FALSE`/`OP_0` followed by `OP_RETURN`-style invalidation; rust-bitcoin exposes `is_provably_unspendable`).
- Optionally require standardness (`bitcoin::policy`/`is_standard` checks or restricting to known templates: P2PKH/P2SH/P2WPKH/P2WSH/P2TR) so the signed transaction is guaranteed relayable.

### Proof of Concept
```rust
// A withdrawal destination that is provably unspendable:
let unspendable = ScriptBuf::builder()
    .push_opcode(bitcoin::opcodes::all::OP_RETURN)
    .into_script();

// Any non-zero amount >= DUST passes all checks in SignableTransaction::new.
let tx = SignableTransaction::new(
    vec![received_output],              // a real scanned UTXO of the multisig
    &[(unspendable, 100_000)],          // passes the dust check
    Some(change_script),                // valid change offset script
    None,
    fee_per_vbyte,
).unwrap();

// The FROST multisig signs it; once mined, the 100_000 sats are burned forever.
// If instead a merely non-standard script is used, the signed transaction cannot
// be relayed and the entire batch (inputs + change) is frozen.
```

The gap is visible directly in `send.rs`: the `data` path correctly forces `Amount::ZERO` for `OP_RETURN` outputs (lines 194-202), while the `payments` path (lines 187-191) performs no equivalent spendability validation.