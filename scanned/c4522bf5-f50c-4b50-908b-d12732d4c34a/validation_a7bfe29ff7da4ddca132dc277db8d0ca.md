[1](#0-0)

### Citations

**File:** gossip/src/gossip_error.rs (L16-17)
```rust
    #[error(transparent)]
    Io(#[from] io::Error),
```
