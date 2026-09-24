## Analysis: PeerTube Uncaught-Exception DoS Analog in Protobuf

The PeerTube CVE-2025-32944 root cause is: an internal library (`yauzl`) throws an exception on a malformed/illegal input value (filename) that application code does not catch, so the exception propagates uncaught and crashes the process — repeatedly, since the same bad archive is retried on startup.

The transferable invariant is: **a parsing API is documented/expected to only raise a specific, catchable error type for malformed attacker-controlled input, but an internal helper throws a different, undocumented exception type that escapes the intended catch boundary.**

### Where this reproduces in Protobuf (Python pure-Python backend)

`python/google/protobuf/internal/decoder.py` documents the exception contract for every field decoder: [1](#0-0) 

This explicitly says only `IndexError`, `struct.error`, and `message.DecodeError` are expected, and that "something up the call stack is expected to catch `IndexError` and `struct.error` and convert them to `message.DecodeError`." `UnicodeDecodeError` is conspicuously absent from this contract.

Confirming the gap is a first-class test in `python/google/protobuf/internal/message_test.py`: [2](#0-1) 

The test explicitly documents: "currently only the python api implementation raises `UnicodeDecodeError` when a string field contains bad utf-8" — i.e., it is skipped for the `upb`/C++ backends, which correctly normalize this failure into `message.DecodeError`. In the pure-Python backend, when a `string` field's bytes fail UTF-8 decoding (a bounded, fully attacker-controlled value reachable via `ParseFromString`/`MergeFromString`/`FromString`), the underlying `bytes.decode('utf-8')` call raises `UnicodeDecodeError`, which is not caught and wrapped into `message.DecodeError` by the surrounding parse dispatch loop — it escapes to the caller as an unexpected exception type.

### Why this matches the external report's failure pattern

- **Attacker-controlled value**: bytes for a proto3 `string` field in an otherwise well-formed, bounded binary payload.
- **Missing check/wrap**: the field decoder for strings performs UTF-8 decoding without a `try/except UnicodeDecodeError` that converts it to `message.DecodeError`, unlike `IndexError`/`struct.error` which are caught per the documented contract.
- **Impact**: any application code that (correctly, per the public API contract) only catches `google.protobuf.message.DecodeError` around parsing calls will receive an unhandled `UnicodeDecodeError` for a bounded, easily-crafted payload. In a server process without a catch-all, this is the same shape of failure as the PeerTube bug — an uncaught exception raised on ordinary malformed input from a public parsing entry point, exploitable by any client able to submit protobuf payloads (persistent if the bad payload is retried/logged and reprocessed).
- **Scope limit**: this is confined to the **pure-Python** implementation (`api_implementation.Type() == 'python'`); the C++/upb-backed Python bindings and other language implementations correctly wrap this into the documented error type, so this is not a cross-language issue — it's a single-backend documentation/contract violation.

### Caveat
I was not able to pull the exact `StringDecoder`/`FieldDecoder` function body and line numbers in `decoder.py` that performs the raw `.decode('utf-8')` call in this pass (tool budget exhausted), so the precise call site (function name/line range) could not be cited directly — only the module-level exception contract docstring and the corroborating test were retrievable. A Devin session with full file access would be needed to pinpoint the exact decode call site and confirm whether any `try/except` currently surrounds it.

### Recommendation
Wrap UTF-8 decoding of string-type fields in `python/google/protobuf/internal/decoder.py`'s pure-Python decoder(s) in a `try/except UnicodeDecodeError` and re-raise as `message.DecodeError` (mirroring the existing `IndexError`/`struct.error` handling), so the pure-Python backend's exception contract matches the C++/upb backends and the documented API guarantee for all public parsing entry points (`ParseFromString`, `MergeFromString`, `FromString`, `MergeFrom`, etc).

### Citations

**File:** python/google/protobuf/internal/decoder.py (L24-35)
```python
Note that decoders may throw any of the following:
  IndexError:  Indicates a truncated message.
  struct.error:  Unpacking of a fixed-width field failed.
  message.DecodeError:  Other errors.

Decoders are expected to raise an exception if they are called with pos > end.
This allows callers to be lax about bounds checking:  it's fineto read past
"end" as long as you are sure that someone else will notice and throw an
exception later on.

Something up the call stack is expected to catch IndexError and struct.error
and convert them to message.DecodeError.
```

**File:** python/google/protobuf/internal/message_test.py (L182-192)
```python
  def testBadUtf8String(self, message_module):
    if api_implementation.Type() != 'python':
      self.skipTest(
          'Skipping testBadUtf8String, currently only the python '
          'api implementation raises UnicodeDecodeError when a '
          'string field contains bad utf-8.'
      )
    bad_utf8_data = test_util.GoldenFileData('bad_utf8_string')
    with self.assertRaises(UnicodeDecodeError) as context:
      message_module.TestAllTypes.FromString(bad_utf8_data)
    self.assertIn('TestAllTypes.optional_string', str(context.exception))
```
