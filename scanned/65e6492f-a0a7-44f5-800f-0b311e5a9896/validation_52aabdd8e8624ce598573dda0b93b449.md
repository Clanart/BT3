[1](#0-0)

### Citations

**File:** ledger/src/shred/common.rs (L18-22)
```rust
        #[inline]
        fn set_signature(&mut self, signature: Signature) {
            self.payload.as_mut()[..SIZE_OF_SIGNATURE].copy_from_slice(signature.as_ref());
            self.common_header.signature = signature;
        }
```
