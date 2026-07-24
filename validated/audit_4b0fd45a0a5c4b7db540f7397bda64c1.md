I need to trace the full attack path through the code carefully before rendering a verdict.

## Code Trace

**Step 1 — How the payout tx is constructed**

In `Operator::withdraw()`, the operator builds the payout tx using `create_payout_txhandler`, embedding `self.signer.xonly_public_key` in the OP_RETURN: [1](#0-0) 

The user's signature uses `SIGHASH_SINGLE | ANYONECANPAY`, which commits only to the

### Citations

**File:** core/src/operator.rs (L620-626)
```rust
        let payout_txhandler = builder::transaction::create_payout_txhandler(
            input_utxo,
            output_txout,
            self.signer.xonly_public_key,
            in_signature,
            self.config.protocol_paramset().network,
        )?;
```
